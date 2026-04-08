# Varsany Print Automation — Production Deployment Plan

> How the automation will work after deployment
> Date: 2026-03-24
> Business: Varsany / Fullymerched

---

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Component Setup](#component-setup)
3. [Workflow After Deployment](#workflow-after-deployment)
4. [Server Configuration](#server-configuration)
5. [Database Integration](#database-integration)
6. [File Sync & Storage](#file-sync--storage)
7. [Monitoring & Alerts](#monitoring--alerts)
8. [Deployment Checklist](#deployment-checklist)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        AMAZON UK                                │
│  Customer places order → Customization data captured            │
└────────────────┬────────────────────────────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────────────────────────────┐
│              CLOUD DATABASE (Azure/AWS)                         │
│  Microsoft SQL Server: dbAmazonCustomOrders                     │
│  Tables: tblCustomOrder, tblCustomOrderDetails                  │
└────────────────┬────────────────────────────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────────────────────────────┐
│         AUTOMATION SERVER (India - Design Team)                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Python Script: varsany_automation.py                     │  │
│  │ - Polls database every 30 seconds                        │  │
│  │ - Query: IsDesignComplete=0 AND IsOrderProcess=0        │  │
│  │ - Processes orders automatically                         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Processing Steps:                                        │  │
│  │ 1. Download customer image from URL                      │  │
│  │ 2. Upscale 4x with Real-ESRGAN (low res → 812 DPI)     │  │
│  │ 3. Remove background (if flag set)                       │  │
│  │ 4. Render text with auto-sized font                      │  │
│  │ 5. Generate multi-zone layered PSD                       │  │
│  │ 6. Save to Synology Drive                                │  │
│  │ 7. Update DB: IsDesignComplete=1                         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Output: Z:\Drive DTF Orders\1. Amazon DTF\2026-03-24\         │
│          ├── 205-6487629-5805162_front.psd                     │
│          ├── 205-6487629-5805162_back.psd                      │
│          └── 205-6487629-5805162_sleeve.psd                    │
└────────────────┬────────────────────────────────────────────────┘
                 │
                 ↓ (Synology Sync via SpeedFusion)
┌─────────────────────────────────────────────────────────────────┐
│         SYNOLOGY NAS (UK - Printing Department)                 │
│  Synced folder: Vector Designs\Drive DTF Orders                │
│  Automatic sync every few seconds via Peplink router            │
└────────────────┬────────────────────────────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────────────────────────────┐
│          PRINTING TEAM (UK)                                     │
│  1. Opens PSD file from synced folder                           │
│  2. Reviews each zone (front, back, pocket, sleeve)             │
│  3. Flattens layers in Photoshop                                │
│  4. Sends to DTF printer                                        │
│  5. Prints and ships to customer                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Setup

### A. Automation Server (India Design Team PC)

**Hardware Requirements:**
- Windows 10/11 Pro (64-bit)
- 16GB RAM minimum (32GB recommended)
- SSD with 100GB free space
- Stable internet connection
- Dedicated GPU for Real-ESRGAN upscaling (NVIDIA GTX 1050 or better)

**Software Stack:**
```
C:\Varsany\
├── varsany_automation.py          # Main automation script
├── .env                            # Secrets (DB password, email, Slack)
├── requirements.txt                # Python dependencies
├── Fonts\                          # TTF files for text rendering
├── Templates\                      # 28 PSD templates (one per product/zone)
├── Output\                         # Daily dated folders with PSDs
├── Temp\                           # Temporary processing files
├── Logs\
│   └── varsany_automation.log      # Rotation: 7 days
└── realesrgan\
    └── realesrgan-ncnn-vulkan.exe  # 4x upscaling binary
```

**Python Dependencies:**
```bash
pip install pyodbc pillow python-dotenv rembg
# pyodbc: SQL Server database connection
# pillow: Image processing and text rendering
# python-dotenv: Load .env secrets
# rembg: AI background removal (u2netp model)
```

**Environment Variables (.env):**
```
DB_SERVER=your-server.database.windows.net
DB_DATABASE=dbAmazonCustomOrders
DB_USER=automation_user
DB_PASSWORD=<secure-password>
EMAIL_USER=alerts@fullymerched.com
EMAIL_PASSWORD=<app-password>
SLACK_WEBHOOK=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
SYNOLOGY_DRIVE=Z:\Drive DTF Orders\1. Amazon DTF
PX_PER_CM=320  # Production 812 DPI
```

---

### B. Database Configuration (Azure/AWS SQL Server)

**New SQL User (read/write permissions only):**
```sql
-- Run on live database
USE dbAmazonCustomOrders;

CREATE LOGIN automation_user WITH PASSWORD = '<secure-password>';
CREATE USER automation_user FOR LOGIN automation_user;

-- Grant minimal permissions (read orders, update status)
GRANT SELECT ON tblCustomOrder TO automation_user;
GRANT SELECT, UPDATE ON tblCustomOrderDetails TO automation_user;
```

**Add New Columns (if not already added):**
```sql
ALTER TABLE tblCustomOrderDetails ADD
    IsFrontBgRemove   bit           NULL DEFAULT 0,
    IsBackBgRemove    bit           NULL DEFAULT 0,
    IsPocketBgRemove  bit           NULL DEFAULT 0,
    IsSleeveBgRemove  bit           NULL DEFAULT 0,
    QCStatus          nvarchar(20)  NULL DEFAULT 'pending',
    QCNotes           nvarchar(500) NULL,
    IsComplexOrder    bit           NULL DEFAULT 0,
    OutputFilePath    nvarchar(500) NULL;
```

**Firewall Rules:**
- Open port **1433** (SQL Server)
- Whitelist automation server IP address
- Enable SSL/TLS encryption for database connection

---

### C. Synology Drive Sync

**India Side (Design Team):**
- Map Synology as network drive: `Z:\`
- Folder: `Z:\Drive DTF Orders\1. Amazon DTF\`
- Synology Desktop App installed and running
- Auto-sync enabled (real-time sync)

**UK Side (Printing Team):**
- Same mapped drive structure
- SpeedFusion/Peplink B One router for VPN tunnel
- Bandwidth: Minimum 10 Mbps upload (India) / 10 Mbps download (UK)
- Typical PSD size: 20-80 MB per file
- Sync time: 10-60 seconds depending on file size

**Folder Structure:**
```
Z:\Drive DTF Orders\1. Amazon DTF\
└── 2026-03-24\
    ├── 205-6487629-5805162_front.psd
    ├── 205-6487629-5805162_back.psd
    ├── 205-6487629-5805162_sleeve.psd
    ├── 205-7834521-9234811_front.psd
    └── 205-7834521-9234811_pocket.psd
```

---

## 3. Workflow After Deployment

### **AUTOMATED FLOW (No Human Intervention Needed):**

```
[08:30] Customer places order on Amazon UK
        → T-shirt, Front customization
        → Uploads logo, adds text "Elite Supermarket"

[08:31] Amazon saves to database
        → tblCustomOrderDetails.IsDesignComplete = 0
        → tblCustomOrderDetails.FrontImage = "https://..."
        → tblCustomOrderDetails.FrontText = "Elite Supermarket"

[08:31] Automation script detects new order
        → Polls database: "SELECT * WHERE IsDesignComplete=0"
        → Finds order 205-6487629-5805162

[08:31] Script processes order
        [08:31:02] Download image from Amazon URL
        [08:31:05] Upscale 4x (Real-ESRGAN): 500x400 → 2000x1600px
        [08:31:08] Check background removal flag: IsFrontBgRemove=1
        [08:31:10] Remove white background using rembg
        [08:31:15] Render text "Elite Supermarket" with Arial Bold
        [08:31:16] Auto-size font: 217pt (fits canvas perfectly)
        [08:31:18] Create multi-zone PSD: POCKET (empty) | BACK (empty) | FRONT (image+text)
        [08:31:25] Save: Z:\Drive DTF Orders\1. Amazon DTF\2026-03-24\205-6487629-5805162.psd
        [08:31:26] Update database: IsDesignComplete=1, ProcessBy='AutomationScript'

[08:31] Synology sync starts
        [08:31:27] India → UK sync begins (76.3 MB file)
        [08:32:14] Sync complete (47 seconds)

[08:32] UK printing team notification
        → Slack alert: "✅ New order ready: 205-6487629-5805162"
        → Email sent to printing@fullymerched.com

[08:35] UK printer opens file
        → Opens 205-6487629-5805162.psd in Photoshop
        → Reviews: POCKET (empty) | BACK (empty) | FRONT (logo + text)
        → Confirms quality: Text clear, image high-res, no errors
        → Flattens layers: Layer → Flatten Image
        → Saves as TIF: File → Save As → TIFF (CMYK)

[09:00] Print and ship
        → DTF printer prints onto film
        → Heat press transfers to T-shirt
        → QC check, packing, shipping label
        → Mark as shipped in system
```

### **Time Savings:**
- **Before automation:** 10-15 minutes per order (manual designer work)
- **After automation:** 30-60 seconds per order (fully automated)
- **Designer time freed up:** ~95% reduction in manual work

---

## 4. Server Configuration

### **Option A: Dedicated Windows Server (Recommended)**

**Specs:**
- Windows Server 2019/2022
- 32GB RAM
- 512GB SSD
- NVIDIA GPU (for Real-ESRGAN)
- Static IP on local network

**Setup:**
```powershell
# Install Python 3.10+
winget install Python.Python.3.10

# Create service user
net user VarsanyBot <password> /add
net localgroup Administrators VarsanyBot /add

# Install script as Windows Service
nssm install VarsanyAutomation "C:\Python310\python.exe" "C:\Varsany\varsany_automation.py"
nssm set VarsanyAutomation AppDirectory "C:\Varsany"
nssm set VarsanyAutomation DisplayName "Varsany Print Automation"
nssm set VarsanyAutomation Description "Automated PSD generation for Amazon custom orders"
nssm set VarsanyAutomation Start SERVICE_AUTO_START

# Start service
nssm start VarsanyAutomation
```

**Service will:**
- Start automatically on boot
- Restart automatically if crashed
- Log to `C:\Varsany\Logs\varsany_automation.log`
- Run 24/7 polling database every 30 seconds

---

### **Option B: Existing Designer PC (Simpler Setup)**

**Requirements:**
- PC must stay on during business hours
- Runs as background process (minimal CPU/RAM usage when idle)

**Setup:**
```powershell
# Add to Windows Startup
# Create shortcut: C:\Varsany\start_automation.bat
@echo off
cd C:\Varsany
python varsany_automation.py
pause
```

**Startup folder location:**
```
C:\Users\<username>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\
```

---

## 5. Database Integration

### **Query Logic:**

**1. Poll for new orders every 30 seconds:**
```python
sql = """
    SELECT TOP 10
        d.idCustomOrderDetails,
        d.idCustomOrder,
        o.OrderID,
        o.SKU,
        d.PrintLocation,
        d.IsFrontLocation, d.IsBackLocation,
        d.IsPocketLocation, d.IsSleeveLocation,
        d.FrontImage, d.FrontText, d.FrontFonts, d.FrontColours,
        d.BackImage, d.BackText, d.BackFonts, d.BackColours,
        d.PocketImage, d.PocketText, d.PocketFonts, d.PocketColours,
        d.SleeveImage, d.SleeveText, d.SleeveFonts, d.SleeveColours,
        d.IsFrontBgRemove, d.IsBackBgRemove,
        d.IsPocketBgRemove, d.IsSleeveBgRemove,
        d.FrontPreviewImage
    FROM tblCustomOrderDetails d
    INNER JOIN tblCustomOrder o ON d.idCustomOrder = o.idCustomOrder
    WHERE d.IsDesignComplete = 0
      AND d.IsOrderProcess = 0
      AND o.IsShipped = 0
    ORDER BY d.DateAdd ASC
"""
```

**2. Mark order complete after processing:**
```python
sql = """
    UPDATE tblCustomOrderDetails
    SET IsDesignComplete = 1,
        IsOrderProcess = 1,
        ProcessBy = 'AutomationScript',
        ProcessTime = GETDATE(),
        AdditionalPSD = ?,
        OutputFilePath = ?,
        QCStatus = 'auto_approved'
    WHERE idCustomOrderDetails = ?
"""
```

**3. Handle errors and flag for designer review:**
```python
sql = """
    UPDATE tblCustomOrderDetails
    SET IsComplexOrder = 1,
        QCStatus = 'needs_review',
        QCNotes = ?,
        ProcessBy = 'AutomationScript'
    WHERE idCustomOrderDetails = ?
"""
```

---

## 6. File Sync & Storage

### **Synology Drive Folder Structure:**

```
Z:\Drive DTF Orders\
├── 1. Amazon DTF\
│   ├── 2026-03-24\
│   │   ├── 205-6487629-5805162.psd  (76 MB - Front+Back+Pocket)
│   │   ├── 205-7834521-9234811.psd  (42 MB - Front only)
│   │   └── 205-9182374-4729381.psd  (124 MB - All zones + sleeve)
│   ├── 2026-03-25\
│   └── Archive\
│       └── 2026-03\  (auto-archive after 30 days)
├── 2. QC Review\  (flagged orders needing manual check)
└── 3. Completed\  (printed orders - archived after shipping)
```

### **File Naming Convention:**
```
{OrderID}.psd                       # Multi-zone PSD (all zones in one file)
{OrderID}_front.psd                 # If zones saved separately (legacy)
{OrderID}_back.psd
{OrderID}_sleeve.psd
```

### **Backup Strategy:**
- **Primary:** Synology NAS (India + UK synced)
- **Backup:** Daily snapshot to cloud (AWS S3 or Azure Blob)
- **Retention:** 90 days online, 1 year archived

---

## 7. Monitoring & Alerts

### **Slack Notifications:**
```python
# Success notification
✅ Order 205-6487629-5805162 complete!
   Product: T-Shirt (Black M)
   Zones: FRONT, BACK
   File: 76.3 MB
   Time: 54 seconds
   📂 Z:\Drive DTF Orders\1. Amazon DTF\2026-03-24\205-6487629-5805162.psd

# Error notification
❌ Order 205-7834521-9234811 FAILED
   Error: Premium font 'Glitter Gold' not found
   Action: Designer review required
   🔗 http://crssoft.co.uk/Order/CustomOrder?id=205-7834521-9234811
```

### **Email Alerts:**
- Send to: `printing@fullymerched.com`, `design@fullymerched.com`
- When: Order complete, error occurred, system offline >5 minutes

### **Health Monitoring:**
```python
# Every 5 minutes, send heartbeat
POST https://healthchecks.io/ping/varsany-automation

# Dashboard shows:
- Last successful order: 2 minutes ago ✅
- Orders processed today: 47
- Average processing time: 38 seconds
- Error rate: 2.1% (1 failed out of 47)
- Disk space: 234 GB free
- Database connection: OK
```

---

## 8. Deployment Checklist

### **Phase 1: Pre-Deployment (1-2 days)**

- [ ] **Server Setup**
  - [ ] Install Windows Server or use designer PC
  - [ ] Install Python 3.10+
  - [ ] Install Real-ESRGAN binary
  - [ ] Map Synology drive (Z:\)
  - [ ] Create C:\Varsany folder structure

- [ ] **Database**
  - [ ] Add new columns to tblCustomOrderDetails
  - [ ] Create automation_user SQL login
  - [ ] Open firewall port 1433
  - [ ] Test connection from automation server

- [ ] **Code Deployment**
  - [ ] Copy varsany_automation.py to C:\Varsany\
  - [ ] Create .env file with secrets
  - [ ] Install Python dependencies: `pip install -r requirements.txt`
  - [ ] Copy 28 PSD templates to C:\Varsany\Templates\
  - [ ] Copy font files to C:\Varsany\Fonts\

- [ ] **Testing**
  - [ ] Test database connection
  - [ ] Test Real-ESRGAN upscaling
  - [ ] Test background removal
  - [ ] Test PSD generation with sample order
  - [ ] Test Synology sync (India → UK)

---

### **Phase 2: Pilot Run (1 week)**

- [ ] **Limited Production**
  - [ ] Run automation alongside manual process
  - [ ] Process 10 orders/day automatically
  - [ ] Designer reviews all auto-generated PSDs
  - [ ] Log any issues or improvements needed

- [ ] **Quality Checks**
  - [ ] Compare auto PSD vs manual PSD quality
  - [ ] Check text sizing accuracy
  - [ ] Verify background removal quality
  - [ ] Confirm file sync speed

- [ ] **Performance Tuning**
  - [ ] Optimize Real-ESRGAN settings for speed/quality
  - [ ] Adjust font sizing algorithm if needed
  - [ ] Fine-tune background removal threshold
  - [ ] Set polling interval (30s default)

---

### **Phase 3: Full Production (Go Live)**

- [ ] **Switch to Automation**
  - [ ] Set automation script to process all orders
  - [ ] Designer monitors first 50 orders
  - [ ] Printing team confirms quality acceptable
  - [ ] Switch from LOW-RES to production: `PX_PER_CM=320`

- [ ] **Monitoring Setup**
  - [ ] Configure Slack webhooks
  - [ ] Set up email alerts
  - [ ] Enable healthcheck pings
  - [ ] Create monitoring dashboard

- [ ] **Documentation**
  - [ ] Train design team on reviewing flagged orders
  - [ ] Train printing team on opening PSDs
  - [ ] Create troubleshooting guide
  - [ ] Document common error scenarios

---

### **Phase 4: Ongoing Maintenance**

- [ ] **Daily**
  - [ ] Check Slack for error alerts
  - [ ] Review QC flagged orders
  - [ ] Monitor disk space usage

- [ ] **Weekly**
  - [ ] Review automation logs
  - [ ] Check error rate trend
  - [ ] Archive old PSD files to backup

- [ ] **Monthly**
  - [ ] Update Real-ESRGAN if new version released
  - [ ] Review and optimize database queries
  - [ ] Clean up temp files and old logs
  - [ ] Backup .env and configuration files

---

## 9. Expected Performance After Deployment

### **Processing Speed:**
| Step | Time |
|------|------|
| Download customer image | 1-3 sec |
| Upscale 4x (Real-ESRGAN) | 5-10 sec |
| Remove background | 3-5 sec |
| Render text + layout | 2-4 sec |
| Generate PSD | 5-15 sec |
| Save to Synology | 2-5 sec |
| **Total per order** | **18-42 sec** |

### **Capacity:**
- **Orders per hour:** ~100 (with 30-second polling)
- **Orders per day:** ~2,400 (24-hour operation)
- **Current volume:** ~50-100 orders/day
- **Headroom:** 2400% capacity available for growth

### **Cost Savings:**
- **Designer time saved:** ~10 min/order × 100 orders/day = 16.7 hours/day
- **Equivalent to:** 2 full-time designers
- **Annual savings:** £60,000+ (based on designer salary)

### **Error Handling:**
| Error Type | Automation Response |
|------------|---------------------|
| Premium font missing | Flag for designer, send alert |
| Low resolution image | Flag for QC review |
| Screenshot borders | Auto-detect and crop |
| Complex multi-image | Process if ≤6 images, else flag |
| Background removal uncertain | Process, flag if confidence <80% |
| Database connection lost | Retry 3x, then alert, pause script |
| Synology drive offline | Queue locally, retry every 5 min |

---

## 10. Rollback Plan (If Issues Occur)

**If automation has critical issues:**

1. **Immediate pause:**
   ```powershell
   nssm stop VarsanyAutomation
   ```

2. **Switch to manual process:**
   - Designer resumes manual PSD creation
   - Order page at http://crssoft.co.uk/Order/CustomOrder

3. **Fix and test offline:**
   - Debug on test database
   - Process sample orders
   - Verify fixes

4. **Resume automation:**
   ```powershell
   nssm start VarsanyAutomation
   ```

---

## Summary: Production Deployment

**When deployed, the system will:**

✅ **Automatically monitor** database for new orders every 30 seconds
✅ **Process orders** in 18-42 seconds (vs 10-15 minutes manually)
✅ **Generate high-quality PSDs** at 812 DPI (320 px/cm)
✅ **Sync files** to UK printing team within 10-60 seconds
✅ **Send alerts** via Slack/Email when complete or if errors
✅ **Flag complex orders** for designer review (premium fonts, low res, etc.)
✅ **Save 95% of designer time** for other tasks
✅ **Handle 100+ orders/day** with zero manual intervention

**Key Success Metrics:**
- Automation success rate: >95%
- Average processing time: <60 seconds
- File sync time: <2 minutes India→UK
- Designer review needed: <5% of orders
- Printing team satisfaction: High-quality PSDs ready to print

---

**Next Steps:**
1. Review this deployment plan with Yedhu and Dhruv
2. Schedule installation date
3. Prepare server/PC for automation
4. Run pilot with 10 test orders
5. Go live with full automation

**Questions?** Contact: Claude via VSCode or Yedhu@fullymerched.com
