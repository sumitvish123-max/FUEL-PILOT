# ⛽ FuelPilot — Smart Fuel & CNG Station Management System

A full-stack **Forecourt ERP** designed for Indian petrol, diesel, and CNG stations. FuelPilot brings fuel sales, cash reconciliation, tank inventory, employee attendance, maintenance, and compliance into one dashboard so station owners can monitor operations remotely.

**🌐 Live Demo:** [Launch FuelPilot](https://fuel-pilot-1.onrender.com)  
**📂 Source Code:** [GitHub Repository](https://github.com/sumitvish123-max/FUEL-PILOT)

> **Project status:** Working prototype / pilot. The system currently relies on manual forecourt entries; direct dispenser and automatic tank-gauge integrations are on the roadmap. The Render service may take time to start after inactivity.

---

## 🚀 Key Features

- **Face Recognition Attendance:** Webcam-based staff registration, face check-in/check-out, 128-dimensional embeddings, duplicate-face checks, and attendance scan logs.
- **Shift & Cash Reconciliation:** Calculate expected sales from nozzle totalisers and fuel rates; compare against cash, UPI, card, and credit collections to flag variances.
- **Tank Inventory Monitoring:** Convert dip readings to estimated stock, flag water-in-tank readings, forecast stock availability, and identify reorder risks.
- **Tanker Delivery Tracking:** Compare invoiced and received quantities, identify shortages, and flag density mismatches.
- **Maintenance Management:** Log equipment breakdowns, assign technicians, track repair progress, and monitor preventive maintenance and AMC expiries.
- **Compliance Tracking:** Monitor Weights & Measures stamping, Fire NOC, PESO licence, and pollution consent expiry dates.
- **Owner Dashboard:** View fuel sold, collections, outstanding credit, staff on duty, maintenance tickets, and prioritised alerts.
- **Role-Based Access:** Separate owner and employee permissions, plus a login-free attendance kiosk.

## 🛠️ Architecture & Tech Stack

- **Backend:** Python 3.10–3.12, FastAPI, Uvicorn, SQLAlchemy 2, Pydantic v2.
- **Frontend:** HTML5, CSS3, Vanilla JavaScript (responsive, light and dark themes).
- **Database:** SQLite for local development; configurable PostgreSQL support.
- **Computer Vision:** OpenCV, NumPy, `face_recognition` / dlib.
- **Deployment:** Render, with FastAPI serving both the frontend and REST endpoints.

## 📁 Project Structure

```text
FUEL-PILOT/
├── backend/
│   ├── main.py              # API routes and business logic
│   ├── models.py            # Database models
│   ├── schemas.py           # Request/response validation
│   ├── face_utils.py        # Face recognition utilities
│   ├── database.py          # Database connection
│   ├── seed_demo.py         # Optional sample data
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── .env.example
└── .gitignore
```

## ⚙️ Installation & Setup

**1. Clone the repository**

```bash
git clone https://github.com/sumitvish123-max/FUEL-PILOT.git
cd FUEL-PILOT
```

**2. Create and activate a virtual environment**

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
```

macOS / Linux:

```bash
source .venv/bin/activate
```

**3. Install dependencies**

```bash
pip install -r backend/requirements.txt
pip install --no-deps face-recognition==1.3.0
```

**4. Configure environment variables**

Copy `.env.example` to `.env` and set your own credentials:

```env
FUELPILOT_OWNER_PASSWORD=replace-with-a-strong-password
FUELPILOT_STAFF_PASSWORD=replace-with-another-password
```

**5. Start the application**

```bash
uvicorn backend.main:app --reload --port 8000
```

Open **http://localhost:8000** to access FuelPilot. API documentation is available at **http://localhost:8000/docs**.

## 📊 Core Modules

| Module | Purpose |
| --- | --- |
| Attendance | Face-based employee check-in and check-out |
| Shift Management | Nozzle sales and cash/digital reconciliation |
| Inventory | Tank dips, stock forecasting, and alerts |
| Deliveries | Tanker receipt and shortage monitoring |
| Maintenance | Equipment tickets and service schedules |
| Compliance | Licence and certification expiry tracking |
| Dashboard | Station-wide operational overview |

## 🔐 Security & Privacy

- **Authentication:** Signed, expiring tokens and login rate limiting.
- **Access Control:** Owner-only views for sensitive operational and biometric data.
- **Biometric Storage:** Face embeddings rather than raw staff photos; registration should use informed consent and an appropriate privacy process.
- **Known Limitation:** Face matching does **not** yet provide liveness detection; a photograph or screen could potentially fool it. Do not rely on it for payroll without additional safeguards.
- **Production:** Use HTTPS, persistent storage, backups, individual user accounts, and security testing before real-world deployment.
- **Secrets:** Never commit `.env`, passwords, database files, or biometric data. If secrets were previously committed, rotate them and remove them from repository history.

## 🗺️ Roadmap

- [ ] Automatic dispenser / totaliser integration
- [ ] Automatic Tank Gauge (ATG) integration
- [ ] Meter photo capture with OCR
- [ ] Face liveness and anti-spoofing checks
- [ ] Individual employee accounts and action audit logs
- [ ] WhatsApp shift report notifications
- [ ] Multi-station support

## 🤝 Contributing

Contributions, bug reports, and suggestions are welcome. Please do not include real station data, passwords, or face embeddings in issues or pull requests.

## 📄 License

No licence has been added yet. Until a licence is published, all rights are reserved by the author.
