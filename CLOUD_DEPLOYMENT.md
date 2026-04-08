# Varsany Automation — Cloud Server Deployment (RECOMMENDED)

> Why cloud server is better than running on local PC/server
> Date: 2026-03-24

---

## TL;DR: Why Cloud Server?

**YES, it WILL work on a cloud server - and it's actually BETTER than running on a local PC/server!**

### **Benefits of Cloud Deployment:**

| Feature | Cloud Server ✅ | Local India PC ❌ |
|---------|----------------|------------------|
| **Uptime** | 99.9% (always on) | PC must stay on, power cuts |
| **Speed** | Same datacenter as DB (<1ms) | Internet latency (50-200ms) |
| **Scaling** | Auto-scale at peak times | Fixed capacity |
| **GPU** | Add on-demand | Must buy hardware |
| **Backup** | Automated snapshots | Manual backup |
| **Synology** | No VPN needed (S3 upload) | Needs SpeedFusion VPN |
| **Maintenance** | Managed by AWS/Azure | IT team needed |
| **Cost** | £80-250/month | £1,500 hardware + electricity |
| **Disaster Recovery** | Multi-region backup | Single point of failure |

---

## Architecture: Cloud Server Deployment

```
┌──────────────────────────────────────────────────────────────────┐
│                    AWS EC2 / AZURE VM                            │
│  Region: eu-west-2 (London) - same as database                   │
│  Instance: t3.xlarge (4 vCPU, 16GB RAM)                         │
│  OS: Ubuntu 22.04 LTS                                            │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ varsany_automation.py (systemd service)                     ││
│  │ - Polls Azure/AWS SQL database every 30 seconds             ││
│  │ - Processes orders: download → upscale → render → PSD       ││
│  │ - Uploads to S3/Blob storage                                ││
│  └─────────────────────────────────────────────────────────────┘│
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ↓ (S3/Blob upload)
┌──────────────────────────────────────────────────────────────────┐
│           AWS S3 BUCKET / AZURE BLOB STORAGE                     │
│  Bucket: varsany-psd-files                                       │
│  Path: s3://varsany-psd-files/2026-03-24/                       │
│    ├── 205-6487629-5805162.psd                                   │
│    └── 205-7834521-9234811.psd                                   │
│                                                                   │
│  Public URLs generated for each file                             │
│  Synology Cloud Sync monitors this bucket                        │
└──────────────────────┬───────────────────────────────────────────┘
                       │
         ┌─────────────┴──────────────┐
         ↓                            ↓
┌────────────────────┐      ┌────────────────────┐
│ SYNOLOGY INDIA     │      │ SYNOLOGY UK        │
│ Cloud Sync enabled │      │ Cloud Sync enabled │
│ Downloads from S3  │      │ Downloads from S3  │
│ Z:\Drive DTF...\   │      │ Z:\Drive DTF...\   │
└────────────────────┘      └──────┬─────────────┘
                                   │
                                   ↓
                         ┌──────────────────────┐
                         │ PRINTING TEAM (UK)   │
                         │ Opens PSD from sync  │
                         └──────────────────────┘
```

**Key Advantage:** No VPN needed between India ↔ UK!
Both Synology units download from same S3 bucket independently.

---

## Option 1: AWS EC2 (Recommended)

### **Server Specs:**

```yaml
Instance Type: t3.xlarge
- 4 vCPU
- 16GB RAM
- EBS Storage: 100GB SSD (gp3)
- Network: Up to 5 Gbps

Optional GPU for Real-ESRGAN:
Instance Type: g4dn.xlarge
- 4 vCPU
- 16GB RAM
- GPU: NVIDIA T4 (16GB VRAM)
- Cost: £250/month (vs £80/month without GPU)

Region: eu-west-2 (London)
OS: Ubuntu 22.04 LTS
```

### **Monthly Costs:**

| Resource | Cost |
|----------|------|
| EC2 t3.xlarge (24/7) | £60 |
| 100GB SSD storage | £10 |
| S3 storage (1TB) | £20 |
| Data transfer out | £10 |
| **Total (without GPU)** | **£100/month** |
| **With GPU (g4dn.xlarge)** | **£250/month** |

### **Setup Steps:**

**1. Launch EC2 Instance:**
```bash
# AWS Console → EC2 → Launch Instance
AMI: Ubuntu Server 22.04 LTS
Instance Type: t3.xlarge
Storage: 100GB gp3 SSD
Security Group:
  - Inbound: SSH (port 22) from your IP
  - Outbound: All traffic
Key Pair: Create new (varsany-automation.pem)
```

**2. Connect and Install:**
```bash
# SSH into server
ssh -i varsany-automation.pem ubuntu@ec2-xx-xx-xx-xx.eu-west-2.compute.amazonaws.com

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and dependencies
sudo apt install python3.10 python3-pip unzip -y
pip3 install pyodbc pillow python-dotenv rembg boto3

# Install Real-ESRGAN (CPU version)
wget https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip
unzip realesrgan-ncnn-vulkan-20220424-ubuntu.zip -d /home/ubuntu/realesrgan
chmod +x /home/ubuntu/realesrgan/realesrgan-ncnn-vulkan

# Create project directory
mkdir -p /home/ubuntu/varsany/{Fonts,Templates,Temp,Logs}
```

**3. Upload Code:**
```bash
# From your local machine
scp -i varsany-automation.pem varsany_automation.py ubuntu@ec2-xx-xx-xx-xx:~/varsany/
scp -i varsany-automation.pem .env ubuntu@ec2-xx-xx-xx-xx:~/varsany/
scp -i varsany-automation.pem -r Fonts/* ubuntu@ec2-xx-xx-xx-xx:~/varsany/Fonts/
scp -i varsany-automation.pem -r Templates/* ubuntu@ec2-xx-xx-xx-xx:~/varsany/Templates/
```

**4. Configure S3 Upload:**

Update `varsany_automation.py`:
```python
import boto3

# After generating PSD
def save_to_cloud(local_path, order_id):
    s3 = boto3.client('s3',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY'),
        aws_secret_access_key=os.getenv('AWS_SECRET_KEY'),
        region_name='eu-west-2'
    )

    today = datetime.now().strftime("%Y-%m-%d")
    s3_key = f"psd-files/{today}/{os.path.basename(local_path)}"

    s3.upload_file(local_path, 'varsany-psd-files', s3_key)

    # Get public URL
    url = f"https://varsany-psd-files.s3.eu-west-2.amazonaws.com/{s3_key}"

    # Update database with URL
    update_database(order_id, output_url=url)

    # Clean up local file
    os.remove(local_path)

    return url
```

**5. Create systemd Service:**
```bash
sudo nano /etc/systemd/system/varsany-automation.service

[Unit]
Description=Varsany Print Automation
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/varsany
Environment="PATH=/usr/bin:/usr/local/bin"
ExecStart=/usr/bin/python3 /home/ubuntu/varsany/varsany_automation.py
Restart=always
RestartSec=10
StandardOutput=append:/home/ubuntu/varsany/Logs/automation.log
StandardError=append:/home/ubuntu/varsany/Logs/automation_error.log

[Install]
WantedBy=multi-user.target

# Enable and start
sudo systemctl enable varsany-automation
sudo systemctl start varsany-automation

# Check status
sudo systemctl status varsany-automation

# View logs
tail -f /home/ubuntu/varsany/Logs/automation.log
```

**6. Configure S3 Bucket:**
```bash
# AWS Console → S3 → Create Bucket
Bucket name: varsany-psd-files
Region: eu-west-2 (London)
Block public access: OFF (make files publicly readable)
Versioning: Enabled
Lifecycle policy: Archive to Glacier after 90 days

# Bucket Policy (allow public read):
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PublicReadGetObject",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::varsany-psd-files/*"
  }]
}

# IAM User for automation script:
Username: varsany-automation
Policy: AmazonS3FullAccess
Generate Access Key + Secret Key → add to .env file
```

---

## Option 2: Azure VM (If database is already on Azure)

### **Server Specs:**

```yaml
Instance: Standard_D4s_v3
- 4 vCPU
- 16GB RAM
- 128GB Premium SSD
- Region: UK South (same as Azure SQL database)
- OS: Ubuntu 22.04 LTS
- Cost: £90/month
```

### **Advantages:**
- ✅ Same datacenter as database = <1ms latency
- ✅ Private networking (no internet exposure)
- ✅ Integrated monitoring with Azure Monitor
- ✅ Azure Blob Storage instead of S3

### **Setup:**
```bash
# Azure Portal → Virtual Machines → Create
Image: Ubuntu Server 22.04 LTS
Size: Standard_D4s_v3
Region: UK South
Authentication: SSH public key
Networking: Same VNet as SQL database

# Install (same as AWS)
sudo apt update && sudo apt install python3.10 python3-pip -y
pip3 install pyodbc pillow python-dotenv rembg azure-storage-blob

# Upload to Azure Blob instead of S3:
from azure.storage.blob import BlobServiceClient

connection_string = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
blob_service = BlobServiceClient.from_connection_string(connection_string)
container_client = blob_service.get_container_client("psd-files")

with open(local_path, "rb") as data:
    blob_client = container_client.upload_blob(
        name=f"{today}/{filename}",
        data=data,
        overwrite=True
    )

url = blob_client.url
```

---

## Synology Cloud Sync Configuration

**Both India and UK Synology NAS:**

1. **Install Cloud Sync App:**
   - Synology Package Center → Search "Cloud Sync" → Install

2. **Configure AWS S3 Connection:**
   ```
   Cloud Provider: Amazon S3
   Access Key: <AWS_ACCESS_KEY>
   Secret Key: <AWS_SECRET_KEY>
   Bucket: varsany-psd-files
   Region: eu-west-2

   Local Path: /Vector Designs/Drive DTF Orders/1. Amazon DTF/
   Remote Path: /psd-files/

   Sync Direction: Download remote changes only
   Sync Interval: Every 1 minute
   ```

3. **Result:**
   - New PSDs uploaded to S3 appear on both Synology units within 1-2 minutes
   - No VPN needed between India ↔ UK
   - Files sync automatically

---

## Monitoring & Alerts (CloudWatch / Azure Monitor)

### **AWS CloudWatch:**

```bash
# Install CloudWatch agent on EC2
wget https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
sudo dpkg -i amazon-cloudwatch-agent.deb

# Monitor:
- CPU usage (alert if >80% for 5 minutes)
- Memory usage (alert if >90%)
- Disk space (alert if <10GB free)
- Process count (alert if varsany_automation.py not running)

# Custom metrics:
- Orders processed per hour
- Average processing time
- Error rate
- PSD file size distribution
```

### **Slack/Email Alerts:**

```python
# In varsany_automation.py
import requests

def send_alert(message, level="info"):
    # Slack
    slack_webhook = os.getenv('SLACK_WEBHOOK')
    requests.post(slack_webhook, json={"text": message})

    # CloudWatch custom metric
    cloudwatch = boto3.client('cloudwatch', region_name='eu-west-2')
    cloudwatch.put_metric_data(
        Namespace='VarsanyAutomation',
        MetricData=[{
            'MetricName': 'OrdersProcessed',
            'Value': 1,
            'Unit': 'Count'
        }]
    )
```

---

## Cost Comparison: Cloud vs On-Premise

| Item | Cloud (AWS) | On-Premise India Server |
|------|-------------|------------------------|
| **Hardware** | £0 (included) | £1,500 (one-time) |
| **GPU** | £150/month (on-demand) | £600 (one-time) |
| **Electricity** | Included | £30/month |
| **Internet** | Included | £50/month |
| **Maintenance** | Included | £100/month (IT staff) |
| **Backup** | Included | £20/month (cloud backup) |
| **Total Year 1** | £1,200 | £3,900 |
| **Total Year 2+** | £1,200/year | £2,400/year |

**Conclusion:** Cloud is cheaper in Year 1 and comparable long-term, with much better reliability!

---

## Deployment Checklist (Cloud Server)

### **Pre-Deployment:**
- [ ] Create AWS account (or use existing)
- [ ] Launch EC2 instance (t3.xlarge, London region)
- [ ] Create S3 bucket (varsany-psd-files)
- [ ] Create IAM user with S3 access
- [ ] Update database firewall (allow EC2 IP)
- [ ] Install Synology Cloud Sync on both NAS units

### **Deployment:**
- [ ] SSH into EC2 instance
- [ ] Install Python + dependencies
- [ ] Upload varsany_automation.py
- [ ] Configure .env with AWS keys
- [ ] Test database connection from EC2
- [ ] Test S3 upload with dummy file
- [ ] Create systemd service
- [ ] Start service and monitor logs

### **Testing:**
- [ ] Process 1 test order manually
- [ ] Verify PSD appears in S3
- [ ] Verify Synology downloads file (India + UK)
- [ ] Check Slack/email alert received
- [ ] Review CloudWatch metrics

### **Go Live:**
- [ ] Switch from LOW-RES to production (PX_PER_CM=320)
- [ ] Monitor first 50 orders
- [ ] Printing team confirms quality
- [ ] Full automation activated!

---

## Disaster Recovery

**Scenario: EC2 instance fails**

1. **Automatic recovery (5 minutes):**
   - AWS Auto Recovery attempts to restart instance
   - systemd restarts service automatically

2. **Manual failover (15 minutes):**
   ```bash
   # Launch new EC2 from AMI snapshot
   # Update DNS/IP in database connection string
   # Service resumes
   ```

3. **Database failover:**
   - Azure SQL has built-in high availability
   - Automatic failover to secondary region

4. **S3 replication:**
   - Enable cross-region replication to eu-west-1
   - If London region fails, switch to Ireland bucket

**RTO (Recovery Time Objective):** 15 minutes
**RPO (Recovery Point Objective):** 0 (no data loss, orders in database)

---

## Summary: Why Cloud Server is Best

✅ **99.9% uptime** - No need to keep PC on 24/7
✅ **Ultra-fast** - Same datacenter as database (<1ms queries)
✅ **Scalable** - Add GPU or scale to multiple servers at peak times
✅ **No VPN** - S3 eliminates SpeedFusion complexity
✅ **Automated backups** - Daily snapshots + S3 versioning
✅ **Monitoring** - CloudWatch alerts before problems occur
✅ **Cost-effective** - £100/month vs £1,500 hardware + maintenance
✅ **Global reach** - Easy to add US/EU regions in future

**Deployment Time:** 2-3 hours
**Payback Period:** Immediate (no hardware purchase)
**Recommendation:** Start with t3.xlarge (no GPU), add GPU later if needed

---

**Questions?**
Contact: Yedhu@fullymerched.com
