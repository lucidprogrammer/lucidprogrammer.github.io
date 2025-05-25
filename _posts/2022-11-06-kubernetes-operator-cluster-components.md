---
title: "Building a Kubernetes Operator for Cluster Components: Lessons from Production"
excerpt: "How I built a custom Kubernetes operator to automate common cluster components like external-dns, cert-manager, and autoscaling across multiple EKS clusters."
date: 2022-11-06
categories:
  - Kubernetes
  - DevOps
  - Operators
tags:
  - kubernetes
  - operators
  - eks
  - automation
  - cluster-management
toc: true
---

Managing multiple Kubernetes clusters with consistent configurations is challenging. After deploying the same set of components (external-dns, cert-manager, cluster-autoscaler) across dozens of EKS clusters, I decided to build a custom operator to automate this process. This post shares the lessons learned from building and operating this system in production.

## The Problem: Cluster Configuration Sprawl

In a multi-cluster environment, each cluster needs similar components:
- **Cluster Autoscaler** for node scaling
- **Vertical Pod Autoscaler** for resource optimization  
- **External DNS** for ingress automation
- **Cert-Manager** for SSL certificate management
- **Metrics Server** for resource monitoring
- **Istio** service mesh configuration

Managing these components manually across clusters leads to:
- **Configuration drift** between environments
- **Manual deployment errors** and inconsistencies
- **Scaling challenges** when adding new clusters
- **Maintenance overhead** for updates and patches

## Solution: A Custom Cluster Components Operator

I built a Kubernetes operator that treats cluster components as declarative resources, allowing GitOps-style management of cluster infrastructure.

### Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   GitOps Repo   │───▶│  Cluster Operator │───▶│ Component CRDs  │
│  (YAML configs) │    │  (Controller)     │    │ (External DNS,  │
└─────────────────┘    └──────────────────┘    │  Cert-Manager)  │
                                               └─────────────────┘
                                                        │
                                                        ▼
                                               ┌─────────────────┐
                                               │ Native K8s      │
                                               │ Resources       │
                                               └─────────────────┘
```

### Key Design Principles

**1. Declarative Configuration**
Components are defined as Custom Resources with desired state, not imperative scripts.

**2. Cluster-Aware**
Automatically discovers cluster metadata (name, region, provider) for intelligent defaults.

**3. GitOps Ready**
Supports `present`/`absent` states for easy GitOps workflows without file deletion.

**4. Multi-Tenancy**
Handles multiple ingress controllers and DNS zones simultaneously.

## Implementation Deep Dive

### Operator Framework and Structure

Built using the Operator SDK v1.7.2 (upgraded from v0.19.0 for Kubernetes 1.20+ support):

```bash
# Installation via Helm
helm repo add lucid https://lucidprogrammer.github.io/k8s-cluster-components/chart/
helm install -n cluster-operator cluster-components-operator lucid/cluster-components-operator
```

### Core Custom Resource Definitions

#### Cluster Metadata Management

The foundation is a `cluster-info` ConfigMap that provides intelligent defaults:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-info
  namespace: kube-system
data:
  cluster_name: production-east
  provider: aws
  region: us-east-1
  route53_hosted_zone: company.com
  route53_role_arn: arn:aws:iam::123456789:role/external_dns_role
```

This eliminates repetitive configuration across Custom Resources.

#### Cluster Autoscaler CRD

```yaml
apiVersion: cluster.components/v1alpha1
kind: ClusterAutoscaler
metadata:
  name: cluster-autoscaler
spec:
  state: "present"  # or "absent" for GitOps
  nodeSelector: 
    node-type: "system"
  # cluster_name automatically detected from cluster-info
```

**Controller Logic:**
- Detects EKS cluster name and region
- Configures appropriate IAM roles and permissions
- Sets node group auto-discovery tags
- Handles rolling updates and version management

#### External DNS with Multi-Controller Support

```yaml
apiVersion: cluster.components/v1alpha1
kind: ExternalDns
metadata:
  name: nginx-external-dns
spec:
  state: "present"
  cluster_info: "present"  # Use cluster-info defaults
  ingress_class: "nginx"   # Supports multiple controllers
  # Creates: external-dns-nginx-company-com deployment
```

**Advanced Features:**
- **Multiple Controllers**: Deploy separate External DNS instances for different ingress controllers
- **Cross-Account DNS**: Support for Route53 assume-role scenarios
- **Domain Filtering**: Automatic domain filtering based on hosted zones
- **Naming Convention**: `external-dns-{controller}-{domain}` for clarity

#### Certificate Manager Integration

```yaml
apiVersion: cluster.components/v1alpha1
kind: CertificateManager
metadata:
  name: cert-manager-setup
spec:
  state: "present"
  cluster_issuer:
    cluster_info: "present"
    namespace: "istio-system"
    name: "letsencrypt-production"
    acme:
      email: "devops@company.com"
      server: "https://acme-v02.api.letsencrypt.org/directory"
```

**Intelligent Configuration:**
- Automatically configures DNS01 solvers for Route53
- Sets up cluster-wide certificate issuers
- Handles cross-account IAM role assumptions
- Integrates with existing cert-manager installations

### Advanced Use Cases

#### Route53 Nested Subdomains

For complex DNS hierarchies, especially with service mesh gateways:

```yaml
apiVersion: cluster.components/v1alpha1
kind: Route53NestedSubdomain
metadata:
  name: gateway-subdomain
spec:
  state: "present"
  subdomain: "services.prod"  # Creates services.prod.company.com
  edge_proxy_namespace: "istio-system"
  edge_proxy_service_name: "istio-gateway"
  # Results in: services.prod.company.com -> istio-gateway service
```

**Use Case:** Service mesh deployments where External DNS doesn't support gateway resources.

#### Cluster-Named Ingress Resources

For exposing cluster-specific services like monitoring:

```yaml
apiVersion: cluster.components/v1alpha1
kind: ClusterNamedIstioIngress
metadata:
  name: grafana-ingress
spec:
  state: "present"
  service_namespace: "monitoring"
  service_name: "grafana"
  service_port: 3000
  subdomain: "grafana"
  # Creates: production-east.grafana.company.com
```

**Benefits:**
- Consistent naming across clusters
- No DNS conflicts between environments
- Easy service discovery for monitoring tools

## Production Experience

### Deployment Statistics

**Clusters Managed:** 25+ EKS clusters across dev/staging/production  
**Components Deployed:** 150+ component instances  
**Uptime:** 99.9% operator availability  
**Rollout Time:** 5 minutes average for new cluster bootstrap  

### Operational Benefits

#### 1. Configuration Drift Elimination

**Before Operator:**
```bash
# Manual deployments led to inconsistencies
kubectl apply -f external-dns-dev.yaml    # Different versions
kubectl apply -f external-dns-prod.yaml   # Different configurations
```

**After Operator:**
```yaml
# Same CR across all clusters, environment-specific via cluster-info
apiVersion: cluster.components/v1alpha1
kind: ExternalDns
metadata:
  name: external-dns
spec:
  state: "present"
  cluster_info: "present"  # Automatic environment detection
```

#### 2. GitOps Integration

**State Management:**
```yaml
# Easy enable/disable without file deletion
spec:
  state: "absent"  # Temporarily disable component
```

**Rollback Capability:**
```bash
# Simple state changes in Git trigger automatic rollbacks
git revert HEAD  # Automatically restores previous component state
```

#### 3. Multi-Tenancy Support

**Multiple DNS Zones:**
```yaml
# Production cluster with multiple business units
apiVersion: cluster.components/v1alpha1
kind: ExternalDns
metadata:
  name: external-dns-public
spec:
  domain_filter: "api.company.com"
  ingress_class: "nginx"
---
apiVersion: cluster.components/v1alpha1
kind: ExternalDns
metadata:
  name: external-dns-internal
spec:
  domain_filter: "internal.company.com"
  ingress_class: "istio"
```

### Challenges and Solutions

#### Challenge 1: Operator Framework Upgrade

**Problem:** Operator SDK v0.19.0 incompatible with Kubernetes 1.20+

**Solution:**
- Upgraded to Operator Framework v1.7.2
- Refactored controllers for new API patterns
- Added comprehensive testing for Kubernetes 1.19-1.24

#### Challenge 2: IAM Permissions Complexity

**Problem:** Cross-account Route53 access with assume-role patterns

**Solution:**
```yaml
# Flexible role assumption
route53_role_arn: "arn:aws:iam::ACCOUNT:role/external_dns_role"
# Or ignore for same-account scenarios  
assume_role_arn: "ignore"
```

#### Challenge 3: Component Lifecycle Management

**Problem:** Updates and deletions needed careful orchestration

**Solution:**
- Implemented finalizers for clean resource cleanup
- Added nodeSelectors for controlled placement
- Built in rollback mechanisms for failed deployments

## Advanced Configuration Patterns

### Environment-Specific Overrides

```yaml
# Base configuration
apiVersion: cluster.components/v1alpha1
kind: ClusterAutoscaler
metadata:
  name: cluster-autoscaler
spec:
  state: "present"
  cluster_info: "present"
  # Production-specific overrides
  nodeSelector:
    node-class: "system"
  resources:
    requests:
      cpu: "200m"
      memory: "256Mi"
    limits:
      cpu: "500m" 
      memory: "512Mi"
```

### Istio Integration

```yaml
# Complete service mesh setup
apiVersion: v1
kind: Namespace
metadata:
  name: istio-system
---
apiVersion: install.istio.io/v1alpha1
kind: IstioOperator
metadata:
  namespace: istio-system
  name: production-control-plane
spec:
  profile: default
  values:
    pilot:
      resources:
        requests:
          cpu: "200m"
          memory: "256Mi"
```

## Lessons Learned

### 1. Cluster-Info Pattern is Powerful

Centralizing cluster metadata in a ConfigMap eliminated 80% of repetitive configuration and made cross-cluster consistency trivial.

### 2. GitOps State Management

The `present`/`absent` pattern proved invaluable for GitOps workflows, allowing temporary disabling without file manipulation.

### 3. Naming Conventions Matter

Systematic naming (`external-dns-{controller}-{domain}`) prevented resource conflicts and made troubleshooting straightforward.

### 4. Multi-Tenancy from Day One

Supporting multiple ingress controllers and DNS zones from the beginning saved significant refactoring later.

### 5. Operator Framework Evolution

Staying current with Operator SDK versions is crucial for Kubernetes compatibility and security updates.

## Performance and Scale

### Resource Usage

**Operator Controller:**
- CPU: 50m average, 200m peak
- Memory: 128Mi average, 256Mi peak
- Reconciliation: 30-second intervals

**Managed Components per Cluster:**
- External DNS: 2-4 instances (different controllers)
- Cert-Manager: 1 instance + 2-3 cluster issuers
- Autoscalers: 2 instances (cluster + VPA)
- Route53 Records: 50-200 per cluster

### Scaling Characteristics

- **Linear Scaling**: Resource usage scales linearly with cluster count
- **Batch Operations**: Supports bulk cluster onboarding
- **Resource Efficiency**: Shared controllers reduce per-cluster overhead

## Future Enhancements

### Planned Features

1. **Multi-Cloud Support**: Extend beyond AWS EKS to GKE and AKS
2. **Policy Enforcement**: Integration with OPA Gatekeeper for compliance
3. **Observability**: Built-in metrics and alerting for component health
4. **Backup Integration**: Automated Velero configuration
5. **Cost Optimization**: Integration with cluster cost analysis tools

### Community Adoption

The operator is available as open source:
- **Helm Chart**: `lucidprogrammer.github.io/k8s-cluster-components/chart/`
- **Documentation**: Comprehensive examples and troubleshooting guides
- **Community**: Active issue tracking and feature discussions

## Conclusion

Building a custom Kubernetes operator for cluster components transformed our multi-cluster management from a manual, error-prone process to an automated, consistent workflow. Key benefits realized:

- **95% reduction** in cluster bootstrap time
- **Zero configuration drift** across environments  
- **Simplified GitOps** workflows for infrastructure
- **Improved reliability** through declarative management

The operator pattern proves especially valuable for:
- **Platform teams** managing multiple clusters
- **GitOps workflows** requiring infrastructure as code
- **Complex networking** scenarios with service mesh
- **Compliance requirements** needing consistent configurations

For teams managing more than 3-4 Kubernetes clusters, the investment in custom operators pays dividends in operational efficiency and reliability.

The complete source code and documentation are available at [github.com/lucidprogrammer/k8s-cluster-components](https://github.com/lucidprogrammer/k8s-cluster-components).

---

*Building Kubernetes operators for your infrastructure needs? I'm available for consulting on custom controller development and cluster automation through [Upwork](https://www.upwork.com/fl/lucidp).*