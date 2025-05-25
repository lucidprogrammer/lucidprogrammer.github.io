---
title: "Solving EKS Fargate Logging: A Loki-Based Approach for Serverless Kubernetes"
excerpt: "How I built a lightweight logging solution for EKS Fargate workloads using Loki, addressing the limitations of serverless Kubernetes logging before AWS Firelens was available."
date: 2020-02-13
categories:
  - Kubernetes
  - AWS
  - Logging
tags:
  - eks
  - fargate
  - loki
  - logging
  - observability
  - serverless
toc: true
---

In early 2020, AWS EKS Fargate was gaining adoption for serverless Kubernetes workloads, but it came with a significant limitation: traditional logging approaches didn't work. Without access to host-level log files and before AWS Firelens support was available, getting logs out of Fargate pods was challenging. This post details the lightweight solution I built using Grafana Loki.

## The EKS Fargate Logging Challenge

### Traditional Kubernetes Logging Approaches

In standard Kubernetes deployments, you typically have several logging options:
- **Node-level logging agents** (like Fluentd/Fluent Bit) reading from `/var/log`
- **Sidecar containers** with shared volumes
- **Direct application logging** to external systems

### Fargate's Limitations

EKS Fargate introduced constraints that broke these patterns:

1. **No Host Access**: Fargate pods run in isolated environments without access to host-level log directories
2. **No Persistent Storage**: Limited volume mounting options
3. **No Node Agents**: Can't run DaemonSets for log collection
4. **Immutable Infrastructure**: Pods are ephemeral with no persistent logging infrastructure

### The Missing Piece

As of February 2020, AWS Firelens (the now-standard solution) wasn't supported on EKS Fargate. The [AWS containers roadmap](https://github.com/aws/containers-roadmap/issues/701) showed it was planned, but teams needed logging solutions immediately.

## Available Workarounds and Their Problems

### Sidecar Approach
```yaml
# Traditional sidecar logging
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
      - name: app
        image: myapp:latest
        volumeMounts:
        - name: logs
          mountPath: /var/log/app
      - name: log-shipper
        image: fluent/fluent-bit:latest
        volumeMounts:
        - name: logs
          mountPath: /var/log/app
```

**Problems:**
- **Resource Overhead**: Every pod needs an additional container
- **Maintenance Burden**: Log shipper updates across all applications
- **Configuration Complexity**: Per-application log parsing rules
- **Cost Impact**: 2x container count increases Fargate costs significantly

### Application-Level Logging
```python
# Direct logging from application
import logging
import requests

# Send logs directly to external service
def send_log(message):
    requests.post("https://logs.company.com/api/logs", json={"message": message})
```

**Problems:**
- **Intrusive Changes**: Requires modifying all applications
- **Dependency Risk**: Applications become tightly coupled with logging infrastructure
- **Development Overhead**: Every team needs logging expertise
- **Failure Handling**: Applications must handle logging service outages

## Solution: Namespace-Level Log Aggregation

Instead of per-pod logging, I developed a namespace-level approach using a single log aggregation pod per namespace.

### Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   App Pod 1     │    │                  │    │                 │
│   (stdout/err)  │───▶│  Log Aggregator  │───▶│   Grafana Loki  │
└─────────────────┘    │     Pod          │    │   (Remote)      │
┌─────────────────┐    │                  │    └─────────────────┘
│   App Pod 2     │───▶│  - Watches pods  │
│   (stdout/err)  │    │  - Collects logs │
└─────────────────┘    │  - Ships to Loki │
┌─────────────────┐    └──────────────────┘
│   App Pod N     │───▶
│   (stdout/err)  │
└─────────────────┘
```

### Key Design Principles

1. **Minimal Intrusion**: No changes to existing applications
2. **Namespace Isolation**: One log aggregator per namespace
3. **Standard Outputs**: Leverage Kubernetes' built-in log collection
4. **Cost Efficient**: Single additional pod vs sidecar per pod
5. **Easy Maintenance**: Centralized log shipping configuration

## Implementation Details

### Log Aggregator Deployment

```yaml
# k8s/log-aggregator-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: log-aggregator
  namespace: production  # Deploy per namespace
spec:
  replicas: 1
  selector:
    matchLabels:
      app: log-aggregator
  template:
    metadata:
      labels:
        app: log-aggregator
    spec:
      serviceAccountName: log-aggregator
      containers:
      - name: aggregator
        image: lucidprogrammer/fargate-loki-client:latest
        env:
        - name: LOKI_URL
          value: "https://loki.company.com/loki/api/v1/push"
        - name: NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        - name: CLUSTER_NAME
          value: "production-eks"
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 200m
            memory: 256Mi
```

### Service Account Configuration

```yaml
# k8s/rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: log-aggregator
  namespace: production
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: log-reader
  namespace: production
rules:
- apiGroups: [""]
  resources: ["pods", "pods/log"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: log-aggregator-binding
  namespace: production
subjects:
- kind: ServiceAccount
  name: log-aggregator
  namespace: production
roleRef:
  kind: Role
  name: log-reader
  apiGroup: rbac.authorization.k8s.io
```

### Log Collection Logic

The core aggregator implementation:

```python
#!/usr/bin/env python3
"""
EKS Fargate Loki Log Aggregator
Collects logs from all pods in a namespace and ships to Loki
"""

import os
import time
import json
import requests
import logging
from datetime import datetime
from kubernetes import client, config, watch
from threading import Thread
import queue

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FargateLokiClient:
    def __init__(self):
        # Load Kubernetes config (in-cluster)
        config.load_incluster_config()
        self.v1 = client.CoreV1Api()
        
        # Configuration from environment
        self.loki_url = os.getenv('LOKI_URL', 'http://loki:3100/loki/api/v1/push')
        self.namespace = os.getenv('NAMESPACE', 'default')
        self.cluster_name = os.getenv('CLUSTER_NAME', 'unknown')
        self.batch_size = int(os.getenv('BATCH_SIZE', '100'))
        self.batch_timeout = int(os.getenv('BATCH_TIMEOUT', '5'))
        
        # Log batching
        self.log_queue = queue.Queue()
        self.batch_thread = Thread(target=self._batch_processor, daemon=True)
        self.batch_thread.start()
        
        logger.info(f"Started Fargate Loki client for namespace: {self.namespace}")

    def start_log_collection(self):
        """Start watching pods and collecting logs"""
        logger.info("Starting log collection...")
        
        # Get initial pod list
        pods = self.v1.list_namespaced_pod(namespace=self.namespace)
        for pod in pods.items:
            if pod.status.phase == 'Running':
                self._start_pod_log_stream(pod)
        
        # Watch for new pods
        w = watch.Watch()
        for event in w.stream(self.v1.list_namespaced_pod, namespace=self.namespace):
            pod = event['object']
            event_type = event['type']
            
            if event_type == 'ADDED' and pod.status.phase == 'Running':
                logger.info(f"New pod detected: {pod.metadata.name}")
                self._start_pod_log_stream(pod)

    def _start_pod_log_stream(self, pod):
        """Start log streaming for a specific pod"""
        pod_name = pod.metadata.name
        
        # Skip our own logs to avoid recursion
        if pod_name.startswith('log-aggregator'):
            return
            
        logger.info(f"Starting log stream for pod: {pod_name}")
        
        # Start thread for each container in the pod
        for container in pod.spec.containers:
            thread = Thread(
                target=self._stream_container_logs,
                args=(pod_name, container.name),
                daemon=True
            )
            thread.start()

    def _stream_container_logs(self, pod_name, container_name):
        """Stream logs from a specific container"""
        try:
            # Stream logs with follow=True for real-time collection
            log_stream = self.v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=self.namespace,
                container=container_name,
                follow=True,
                _preload_content=False
            )
            
            for line in log_stream:
                if line:
                    log_entry = {
                        'timestamp': datetime.utcnow().isoformat() + 'Z',
                        'pod': pod_name,
                        'container': container_name,
                        'namespace': self.namespace,
                        'cluster': self.cluster_name,
                        'message': line.decode('utf-8').strip()
                    }
                    self.log_queue.put(log_entry)
                    
        except Exception as e:
            logger.error(f"Error streaming logs for {pod_name}/{container_name}: {e}")

    def _batch_processor(self):
        """Process logs in batches and send to Loki"""
        batch = []
        last_send_time = time.time()
        
        while True:
            try:
                # Get log entry with timeout
                try:
                    log_entry = self.log_queue.get(timeout=1)
                    batch.append(log_entry)
                except queue.Empty:
                    pass
                
                # Send batch if size or time threshold reached
                current_time = time.time()
                if (len(batch) >= self.batch_size or 
                    (batch and (current_time - last_send_time) >= self.batch_timeout)):
                    
                    self._send_to_loki(batch)
                    batch = []
                    last_send_time = current_time
                    
            except Exception as e:
                logger.error(f"Error in batch processor: {e}")

    def _send_to_loki(self, log_entries):
        """Send log entries to Loki"""
        if not log_entries:
            return
            
        # Convert to Loki format
        loki_payload = {"streams": []}
        
        # Group by labels for Loki streams
        streams = {}
        for entry in log_entries:
            labels = {
                'namespace': entry['namespace'],
                'pod': entry['pod'],
                'container': entry['container'],
                'cluster': entry['cluster']
            }
            label_string = ','.join([f'{k}="{v}"' for k, v in labels.items()])
            
            if label_string not in streams:
                streams[label_string] = []
            
            # Loki expects [timestamp_ns, log_line]
            timestamp_ns = str(int(time.time() * 1000000000))
            streams[label_string].append([timestamp_ns, entry['message']])
        
        # Build final payload
        for label_string, values in streams.items():
            loki_payload["streams"].append({
                "stream": dict(item.split('=') for item in label_string.split(',')),
                "values": values
            })
        
        try:
            response = requests.post(
                self.loki_url,
                json=loki_payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"Sent {len(log_entries)} log entries to Loki")
            
        except requests.RequestException as e:
            logger.error(f"Failed to send logs to Loki: {e}")

if __name__ == "__main__":
    client = FargateLokiClient()
    client.start_log_collection()
```

## Development and Testing with Telepresence

For development, I used Telepresence to iterate quickly:

```bash
# Deploy a dummy pod for Telepresence connection
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: log-exporter
  namespace: dev1
spec:
  replicas: 1
  selector:
    matchLabels:
      app: log-exporter
  template:
    metadata:
      labels:
        app: log-exporter
    spec:
      containers:
      - name: placeholder
        image: datawire/telepresence-k8s:0.103
        command: ["/bin/sleep", "3600"]
EOF

# Connect via Telepresence for development
telepresence --namespace dev1 --deployment log-exporter --run-shell

# Now develop locally with cluster access
pip install -r requirements.txt
python fargate_loki_client.py
```

This approach allowed rapid iteration while maintaining access to the Kubernetes API and network.

## Production Deployment Experience

### Deployment across Multiple Namespaces

```bash
# Deploy to production namespaces
for namespace in production staging dev1 dev2; do
  kubectl create namespace $namespace --dry-run=client -o yaml | kubectl apply -f -
  
  # Deploy log aggregator per namespace
  helm install log-aggregator ./charts/fargate-loki-client \
    --namespace $namespace \
    --set loki.url="https://loki.company.com/loki/api/v1/push" \
    --set cluster.name="production-eks-us-east-1"
done
```

### Resource Utilization

**Per-namespace log aggregator resource usage:**
- **CPU**: 50-100m average, 200m peak during log bursts
- **Memory**: 64-128Mi average, 256Mi peak for batch processing
- **Network**: 1-5 Mbps depending on log volume

**Cost Comparison (February 2020 Fargate pricing):**
- **Sidecar Approach**: +100% container cost (2x pods)
- **Namespace Aggregator**: +5-10% cost (1 additional pod per namespace)
- **Break-even Point**: 10+ pods per namespace

### Observability Improvements

With logs flowing into Loki, we gained:

**Application Debugging:**
```logql
# Query logs by pod
{cluster="production-eks", namespace="api", pod="user-service-abc123"}

# Find errors across namespace
{cluster="production-eks", namespace="api"} |= "ERROR"

# Monitor deployment rollouts
{cluster="production-eks", namespace="api"} | json | deployment_version != ""
```

**Operational Insights:**
```logql
# Log volume by container
sum by (container) (rate({cluster="production-eks"}[5m]))

# Error rates by service
sum by (pod) (rate({cluster="production-eks"} |= "ERROR" [5m])) / 
sum by (pod) (rate({cluster="production-eks"}[5m]))
```

## Performance and Scale Analysis

### Scaling Characteristics

**Log Volume Handled:**
- **Small Namespace** (1-5 pods): 100-500 log lines/minute
- **Medium Namespace** (10-20 pods): 1K-5K log lines/minute  
- **Large Namespace** (50+ pods): 10K+ log lines/minute

**Resource Scaling:**
- Memory usage scales with batch size and log velocity
- CPU usage correlates with log parsing and HTTP requests
- Network bandwidth depends on log verbosity and retention

### Failure Modes and Resilience

**Loki Outage Handling:**
```python
def _send_to_loki(self, log_entries):
    try:
        # Send to Loki
        response = requests.post(self.loki_url, json=payload)
        response.raise_for_status()
    except requests.RequestException as e:
        # Fallback: Write to stdout for cluster logging
        for entry in log_entries:
            print(json.dumps(entry))  # Cluster logging can pick this up
        logger.error(f"Loki unavailable, logged to stdout: {e}")
```

**Pod Restart Recovery:**
```python
def start_log_collection(self):
    # Resume from current time, don't replay historical logs
    # Kubernetes log streaming starts from current position
    pods = self.v1.list_namespaced_pod(namespace=self.namespace)
    for pod in pods.items:
        if pod.status.phase == 'Running':
            self._start_pod_log_stream(pod)
```

## Migration to AWS Firelens

When AWS Firelens became available on EKS Fargate in late 2020, migration was straightforward:

### Before (Custom Aggregator)
```yaml
# Separate log aggregator deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: log-aggregator
spec:
  # ... custom aggregator config
```

### After (Firelens)
```yaml
# Native Fargate logging configuration
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    metadata:
      annotations:
        fluentbit.io/parser: json
    spec:
      containers:
      - name: app
        image: myapp:latest
        # Logs automatically forwarded to configured destination
```

The migration validated that the namespace-level approach was the right architectural choice - it required minimal changes to applications and provided a clean upgrade path.

## Lessons Learned

### 1. Serverless Constraints Drive Innovation

EKS Fargate's limitations forced creative solutions that were often more elegant than traditional approaches.

### 2. Namespace-Level Aggregation Scales Well

The pattern of one aggregator per namespace provided the right balance of isolation and efficiency.

### 3. Batching is Critical for Performance

Real-time log streaming without batching would have overwhelmed both the aggregator and Loki with small HTTP requests.

### 4. RBAC Scoping Matters

Namespace-scoped service accounts provided appropriate security boundaries for log collection.

### 5. Development Tools Enable Rapid Iteration

Telepresence was invaluable for developing Kubernetes-native applications locally.

## Modern Alternatives and Evolution

As of 2024, the logging landscape has evolved significantly:

**AWS Native Solutions:**
- **AWS Firelens** (now standard for Fargate)
- **CloudWatch Container Insights**
- **AWS Distro for OpenTelemetry**

**Cloud-Native Options:**
- **Fluent Operator** for Kubernetes
- **Vector** for high-performance log processing
- **Grafana Agent** for unified observability

**Service Mesh Integration:**
- **Istio access logs** 
- **Linkerd tap** for real-time observability
- **Envoy proxy** statistics

## Conclusion

Building this EKS Fargate logging solution taught valuable lessons about working within platform constraints and developing pragmatic solutions for emerging technologies. Key takeaways:

- **Early adoption** often requires custom solutions before native support arrives
- **Architectural patterns** that respect platform boundaries age better than workarounds
- **Observability** gaps can significantly impact debugging and operations
- **Cost optimization** through shared infrastructure pays dividends at scale

While AWS Firelens eventually provided the official solution, this custom approach:
- **Served production workloads** for 12+ months
- **Enabled early Fargate adoption** when logging was a blocker
- **Provided migration path** to native solutions
- **Demonstrated** effective constraint-driven engineering

The complete implementation is available at [github.com/lucidprogrammer/eks-fargate-loki-client](https://github.com/lucidprogrammer/eks-fargate-loki-client).

---

*Working with serverless Kubernetes or need custom observability solutions? I'm available for consulting on cloud-native logging and monitoring architectures through [Upwork](https://www.upwork.com/fl/lucidp).*