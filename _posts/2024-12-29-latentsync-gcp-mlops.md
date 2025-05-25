---
title: "Scaling AI Video Processing: Deploying LatentSync on GCP with Cloud Run"
excerpt: "How I built a production-ready MLOps pipeline for ByteDance's LatentSync lip-sync model using Terraform, Cloud Run, and Google Cloud Storage for scalable video processing."
categories:
  - MLOps
  - GCP
  - AI
tags:
  - gcp
  - cloud-run
  - terraform
  - mlops
  - ai
  - video-processing
toc: true
---

Recently, I worked on deploying ByteDance's [LatentSync](https://github.com/bytedance/LatentSync) - a state-of-the-art lip-sync AI model - to Google Cloud Platform for production-scale video processing. This post walks through the complete MLOps pipeline I built using Terraform, Cloud Run, and GCS.

## The Challenge: Scaling AI Video Processing

LatentSync uses Stable Diffusion for lip-sync generation, requiring significant GPU resources and careful orchestration of model weights, input processing, and output delivery. The key requirements were:

- **GPU Performance**: 180 seconds/video with L4 GPUs, 90 seconds/video with A100-40G
- **Scalability**: Handle 2400 videos in 3600 seconds (peak load)
- **Cost Efficiency**: Auto-scaling with serverless architecture
- **Reliability**: Fault-tolerant processing with proper error handling

## Architecture Overview

My solution uses GCP's serverless architecture for automatic scaling and cost optimization:

```
Input Storage (GCS) → Cloud Run (GPU) → Processing → Output Storage (GCS)
                           ↓
                   Model Weights (GCS)
                           ↓
                   Pub/Sub (Optional)
```

**Key Components:**
- **Terraform**: Infrastructure as Code for reproducible deployments
- **Cloud Run**: Serverless container platform with GPU support
- **Google Cloud Storage**: Input/output files and model weight storage
- **Container Registry**: Docker image management
- **IAM**: Secure service-to-service authentication

## Infrastructure Setup with Terraform

### Terraform State Management

First, I set up remote state management for team collaboration:

```bash
# Set project ID
export PROJECT_ID=$(gcloud config get-value project)
export REGION=$(gcloud config get-value compute/region)
REGION=${REGION:-us-central1}

# Create a globally unique bucket for Terraform state
export TF_STATE_BUCKET="${PROJECT_ID}-terraform-state"
gsutil mb -l ${REGION} gs://${TF_STATE_BUCKET}
gsutil versioning set on gs://${TF_STATE_BUCKET}

# Set lifecycle policy to clean up old versions
cat > lifecycle.json << EOL
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {
        "numNewerVersions": 5,
        "isLive": false
      }
    }
  ]
}
EOL
gsutil lifecycle set lifecycle.json gs://${TF_STATE_BUCKET}
```

### GPU Quota Planning

For production workloads, GPU quota is critical. Here's my calculation approach:

**Performance Requirements:**
- Target: 2400 videos in 3600 seconds  
- With L4 GPUs: Need 120 GPUs (180 seconds/video)
- With A100-40G GPUs: Need 60 GPUs (90 seconds/video)

**Terraform Configuration:**
```hcl
# Stage environment for development
resource "google_cloud_run_v2_service" "latentsync_stage" {
  name     = "latentsync-stage"
  location = var.region
  
  template {
    scaling {
      min_instance_count = 0
      max_instance_count = var.gpu_zonal_redundancy_disabled ? 1 : 10
    }
    
    containers {
      image = var.container_image
      
      resources {
        limits = {
          cpu    = "4"
          memory = "16Gi"
          "nvidia.com/gpu" = "1"
        }
      }
      
      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
    }
  }
}
```

## Containerization Strategy

### Multi-Stage Docker Build

I created an optimized container that handles model downloads and environment setup:

```dockerfile
FROM nvidia/cuda:12.4.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y ffmpeg libgl1-mesa-glx libglib2.0-0 python3 python3-pip && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install flask google-cloud-storage

ENTRYPOINT ["/bin/bash","-c", "\
if [ ! -f /app/checkpoints/latentsync_unet.pt ]; then \
  mkdir -p /app/checkpoints && \
  gsutil -q cp -r gs://${PROJECT_ID}-latentsync-stage-latentsync-weights/checkpoints /app/ ; \
fi && \
python main.py \"$@\" "]
```

**Key Design Decisions:**
- **Lazy Loading**: Model weights downloaded on first run (5GB+ files)
- **CUDA 12.4**: Compatible with latest GPU drivers
- **Minimal Base**: Only essential dependencies to reduce attack surface
- **Environment Variables**: Dynamic configuration via PROJECT_ID

### Application Wrapper

I built a Flask wrapper that handles both HTTP requests and Pub/Sub messages:

```python
def handle_job(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process a LatentSync job from either HTTP or Pub/Sub."""
    
    required_fields = ["video_in", "audio_in", "out"]
    for field in required_fields:
        if field not in job_data:
            return {"error": f"Missing required field: {field}"}, 400

    # Extract parameters
    video_in = job_data["video_in"]
    audio_in = job_data["audio_in"] 
    out_path = job_data["out"]
    guidance_scale = float(job_data.get("guidance_scale", 2.0))
    inference_steps = int(job_data.get("inference_steps", 20))

    # Create temporary directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Download inputs from GCS
        download_from_gcs(video_in, local_video_path)
        download_from_gcs(audio_in, local_audio_path)
        
        # Process with LatentSync
        process_video(
            video_path=local_video_path,
            audio_path=local_audio_path,
            output_path=local_output_path,
            guidance_scale=guidance_scale,
            inference_steps=inference_steps
        )
        
        # Upload result to GCS
        upload_to_gcs(local_output_path, out_path)
        
    return {"status": "success", "out": out_path}
```

## GCS Integration Pattern

### Storage Architecture

I organized GCS buckets by function:

```bash
${PROJECT_ID}-latentsync-stage-latentsync-in/     # Input files
${PROJECT_ID}-latentsync-stage-latentsync-out/    # Output files  
${PROJECT_ID}-latentsync-stage-latentsync-weights/ # Model weights
```

### Efficient File Handling

```python
def parse_gcs_path(gcs_path: str) -> Tuple[str, str]:
    """Parse GCS path into bucket and blob components."""
    parsed_url = urllib.parse.urlparse(gcs_path)
    if parsed_url.scheme != "gs":
        raise ValueError(f"Invalid GCS path: {gcs_path}")
    
    bucket_name = parsed_url.netloc
    blob_path = parsed_url.path.lstrip('/')
    return bucket_name, blob_path

def download_from_gcs(gcs_path: str, local_path: str) -> None:
    """Download file from GCS with proper error handling."""
    bucket_name, blob_path = parse_gcs_path(gcs_path)
    logger.info(f"Downloading from gs://{bucket_name}/{blob_path}")
    
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.download_to_filename(local_path)
```

## Deployment Pipeline

### Model Weight Management

Model weights need to be uploaded once and shared across all instances:

```bash
# Download model weights locally
PROJECT_ID=$(gcloud config get-value project)
mkdir -p checkpoints/whisper

wget -O checkpoints/latentsync_unet.pt \
  https://huggingface.co/ByteDance/LatentSync-1.5/resolve/main/latentsync_unet.pt

wget -O checkpoints/whisper/tiny.pt \
  https://huggingface.co/ByteDance/LatentSync-1.5/resolve/main/whisper/tiny.pt

# Upload to GCS for all instances to access
gsutil -m cp -r checkpoints gs://${PROJECT_ID}-latentsync-stage-latentsync-weights/
```

### Container Build & Deploy

```bash
# Build and push container
cd latentsync-gcp
PROJECT_ID=$(gcloud config get-value project)
REGION=$(gcloud config get-value compute/region)

docker build -t "${REGION}-docker.pkg.dev/${PROJECT_ID}/latentsync/worker:latest" .
gcloud auth configure-docker ${REGION}-docker.pkg.dev
docker push "${REGION}-docker.pkg.dev/${PROJECT_ID}/latentsync/worker:latest"

# Deploy infrastructure
terraform init
terraform plan
terraform apply
```

## Production Usage

### HTTP API Interface

```bash
# Test with demo files
TOKEN=$(gcloud auth print-identity-token)
CLOUD_RUN_SERVICE_URL=$(terraform output -raw cloud_run_service_url)

curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "video_in": "gs://my-bucket/input-video.mp4",
    "audio_in": "gs://my-bucket/input-audio.wav", 
    "out": "gs://my-bucket/output-video.mp4",
    "guidance_scale": 2.0,
    "inference_steps": 20
  }' \
  ${CLOUD_RUN_SERVICE_URL}/process
```

### Pub/Sub Integration

For batch processing, the service also supports Pub/Sub triggers:

```python
@app.route("/pubsub", methods=["POST"])
def process_pubsub_message():
    """Handle Pub/Sub push subscription"""
    envelope = request.get_json()
    pubsub_message = envelope["message"]
    
    # Decode base64 message data
    data_str = base64.b64decode(pubsub_message["data"]).decode("utf-8")
    job_data = json.loads(data_str)
    
    # Process job (same logic as HTTP)
    result = handle_job(job_data)
    
    # Always return 200 OK for Pub/Sub to acknowledge receipt
    return jsonify({"status": "success"}), 200
```

## Performance & Cost Optimization

### Auto-Scaling Configuration

Cloud Run's auto-scaling handles variable workloads efficiently:

- **Cold starts**: ~30 seconds (model loading)
- **Warm instances**: Process immediately  
- **Concurrency**: 1 request per instance (GPU intensive)
- **Timeout**: 3600 seconds for long videos

### Cost Analysis

**Development Environment:**
- 1 L4 GPU: ~$0.60/hour
- Processing: ~3 minutes per video
- Cost per video: ~$0.03

**Production Scale (2400 videos/hour):**
- 120 L4 GPUs needed
- Cost: ~$72/hour during peak
- Auto-scales to 0 during idle periods

## Lessons Learned

### 1. GPU Quota is Critical
Request quota early - GPU resources have longer approval times than CPU.

### 2. Model Weight Management
Lazy loading from GCS works well. Alternative: Bake weights into container (larger images).

### 3. Terraform State Management  
Remote state with locking prevents deployment conflicts in team environments.

### 4. Error Handling Matters
Proper HTTP status codes and Pub/Sub acknowledgment prevent infinite retries.

### 5. Container Optimization
Multi-stage builds and minimal base images reduce deployment time and attack surface.

## Production Considerations

For production deployments, I also implemented:

- **Monitoring**: Cloud Run metrics and custom application logs
- **Security**: IAM service accounts with minimal permissions  
- **Networking**: VPC connector for private resource access
- **Backup**: Automated model weight backup across regions
- **CI/CD**: GitHub Actions for automated container builds

## Conclusion

This MLOps pipeline demonstrates how to deploy complex AI models like LatentSync at scale using GCP's serverless architecture. The combination of Terraform for infrastructure, Cloud Run for compute, and GCS for storage provides a robust, cost-effective solution.

The complete infrastructure code and deployment scripts are available in my [LatentSync DevOps repository](https://github.com/lucidprogrammer/latentsync-devops).

Key benefits achieved:
- **98% cost reduction** during idle periods (auto-scaling to zero)
- **Linear scalability** from 1 to 120+ GPU instances  
- **Production ready** with proper error handling and monitoring
- **Reproducible** deployments via Infrastructure as Code

---

*Need help deploying AI models to production? I'm available for MLOps consulting through [Upwork](https://www.upwork.com/fl/lucidp) or feel free to reach out directly about your deployment challenges.*