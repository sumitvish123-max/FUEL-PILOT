"""Pydantic v2 request / response schemas."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Product = Literal["PETROL", "DIESEL", "CNG"]
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
TicketCategory = Literal["NOZZLE_LEAK", "HOSE_BURST", "SLOW_DISPENSING", "COMPRESSOR_LOW_PRESSURE",
                         "TOTALISER_JUMPING", "DISPLAY_FAULT", "ELECTRICAL", "OTHER"]
TicketStatus = Literal["OPEN", "TECHNICIAN_ASSIGNED", "PARTS_REPLACED", "RESOLVED"]
DocType = Literal["WM_CALIBRATION", "FIRE_NOC", "EXPLOSIVES_LICENSE", "POLLUTION", "OTHER"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginIn(BaseModel):
    role: Literal["owner", "staff"]
    password: str = Field(min_length=1, max_length=200)


class LoginOut(BaseModel):
    token: str
    role: Literal["owner", "staff"]
    expires_in: int


# ───────────── Staff / face ─────────────
class StaffRegister(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    role: str = Field(min_length=2, max_length=40)
    phone: Optional[str] = Field(default=None, max_length=20)
    shift_name: Literal["MORNING", "EVENING", "NIGHT", "GENERAL"] = "MORNING"
    shift_start: str = Field(default="06:00", pattern=HHMM)
    shift_end: str = Field(default="14:00", pattern=HHMM)
    assigned_nozzles: list[str] = []
    photos: list[str] = Field(min_length=1, max_length=8, description="base64 images / data-URLs, one face each")
    consent: bool = Field(description="Staff member has consented to biometric storage (DPDP Act 2023)")

    @field_validator("consent")
    @classmethod
    def _need_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Biometric consent is required to register a face")
        return v


class FaceCheckIn(BaseModel):
    frame: str


class StaffOut(ORM):
    id: int
    employee_code: str
    name: str
    role: str
    phone: Optional[str]
    shift_name: str
    shift_start: str
    shift_end: str
    assigned_nozzles: list[str]
    photo_count: int
    face_engine: str
    is_active: bool
    created_at: datetime


class StaffRegisterOut(BaseModel):
    staff: StaffOut
    embeddings_stored: int
    landmark_groups: int
    message: str


class VerifyIn(BaseModel):
    frames: list[str] = Field(min_length=1, max_length=5, description="1-5 consecutive webcam frames")
    action: Literal["AUTO", "IN", "OUT"] = "AUTO"


class AttendanceOut(BaseModel):
    id: int
    staff_id: int
    staff_name: str
    staff_role: str
    day: date
    in_time: datetime
    out_time: Optional[datetime]
    status: str
    worked_minutes: Optional[int]


class VerifyOut(BaseModel):
    verified: bool
    action: Optional[Literal["IN", "OUT"]] = None
    message: str
    distance: Optional[float] = None
    staff: Optional[StaffOut] = None
    attendance: Optional[AttendanceOut] = None
    late: bool = False


class ScanLogOut(BaseModel):
    id: int
    ts: datetime
    staff_name: Optional[str]
    success: bool
    distance: Optional[float]
    reason: str


# ───────────── Rates / nozzles ─────────────
class RateIn(BaseModel):
    product: Product
    rate: float = Field(gt=0, lt=1000)


class NozzleOut(ORM):
    id: int
    code: str
    dispenser: str
    product: str
    unit: str
    last_closing: Optional[float] = None
    rate: Optional[float] = None


# ───────────── Shifts ─────────────
class ShiftOpen(BaseModel):
    shift_name: Literal["MORNING", "EVENING", "NIGHT", "GENERAL"]
    manager_id: Optional[int] = None
    openings: dict[str, float] = Field(default_factory=dict,
                                       description="nozzle_code -> opening totaliser. Omit to carry over last closing.")


class VoucherIn(BaseModel):
    customer_name: str = Field(min_length=2, max_length=80)
    vehicle_no: Optional[str] = None
    voucher_no: Optional[str] = None
    amount: float = Field(gt=0)


class ClosingReading(BaseModel):
    nozzle_code: str
    closing: float = Field(ge=0)
    test_litres: float = Field(default=0, ge=0, le=50)


class ShiftClose(BaseModel):
    readings: list[ClosingReading]
    cash: float = Field(default=0, ge=0)
    upi: float = Field(default=0, ge=0)
    card: float = Field(default=0, ge=0)
    credit: float = Field(default=0, ge=0, description="Ignored when vouchers are supplied (their sum is used)")
    vouchers: list[VoucherIn] = []
    notes: Optional[str] = None


class ReadingOut(BaseModel):
    nozzle_code: str
    product: str
    unit: str
    opening: float
    closing: Optional[float]
    test_litres: float
    sold_qty: float
    rate: float
    expected_amount: float
    opening_mismatch: bool


class ShiftOut(BaseModel):
    id: int
    day: date
    shift_name: str
    manager_name: Optional[str]
    status: str
    opened_at: datetime
    closed_at: Optional[datetime]
    expected_revenue: float
    cash_amount: float
    upi_amount: float
    card_amount: float
    credit_amount: float
    total_collections: float
    variance: float
    variance_status: str
    severity: str
    tolerance: float
    notes: Optional[str]
    readings: list[ReadingOut] = []
    warnings: list[str] = []


# ───────────── Tanks / dips / deliveries ─────────────
class TankIn(BaseModel):
    code: str = Field(min_length=2, max_length=12)
    product: Product
    kind: Literal["UNDERGROUND", "CASCADE"] = "UNDERGROUND"
    capacity: float = Field(gt=0)
    diameter_mm: float = Field(default=2500, gt=0)
    min_stock: float = Field(default=0, ge=0)
    dead_stock: float = Field(default=0, ge=0)


class DipIn(BaseModel):
    tank_code: str
    reading_type: Literal["OPENING", "CLOSING", "ADHOC"] = "ADHOC"
    dip_mm: float = Field(ge=0, description="Product dip in mm (for CNG cascade: quantity in kg)")
    water_mm: float = Field(default=0, ge=0)
    recorded_by_id: Optional[int] = None


class DipOut(BaseModel):
    id: int
    tank_code: str
    product: str
    ts: datetime
    reading_type: str
    dip_mm: float
    water_mm: float
    volume: float
    water_alert: str          # "" | "WARNING" | "CRITICAL"


class TankOut(BaseModel):
    id: int
    code: str
    product: str
    kind: str
    capacity: float
    min_stock: float
    dead_stock: float
    unit: str
    last_dip_at: Optional[datetime]
    stock: Optional[float]
    fill_pct: Optional[float]
    water_mm: float
    avg_daily_sales: Optional[float]
    days_left: Optional[float]
    reorder_level: Optional[float]
    status: Literal["OK", "LOW", "DRY_RISK", "NO_DATA"]
    water_alert: str


class DeliveryIn(BaseModel):
    tank_code: str
    invoice_no: str = Field(min_length=1, max_length=40)
    supplier: Optional[str] = None
    vehicle_no: Optional[str] = None
    invoiced_qty: float = Field(gt=0)
    pre_dip_mm: float = Field(ge=0)
    post_dip_mm: float = Field(ge=0)
    sales_during: float = Field(default=0, ge=0)
    invoice_density: Optional[float] = Field(default=None, gt=0.6, lt=1.0)
    observed_density: Optional[float] = Field(default=None, gt=0.6, lt=1.0)
    notes: Optional[str] = None


class DeliveryOut(ORM):
    id: int
    tank_code: str
    product: str
    ts: datetime
    invoice_no: str
    supplier: Optional[str]
    vehicle_no: Optional[str]
    invoiced_qty: float
    pre_volume: float
    post_volume: float
    sales_during: float
    received_qty: float
    variance_qty: float
    variance_pct: float
    invoice_density: Optional[float]
    observed_density: Optional[float]
    status: str
    notes: Optional[str]


# ───────────── CMMS ─────────────
class EquipmentIn(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    name: str
    category: Literal["DISPENSER", "COMPRESSOR", "ATG", "OTHER"] = "OTHER"
    amc_vendor: Optional[str] = None
    amc_expiry: Optional[date] = None
    last_service: Optional[date] = None
    pm_interval_days: int = Field(default=90, ge=1, le=730)


class EquipmentOut(ORM):
    id: int
    code: str
    name: str
    category: str
    status: str
    health: int
    amc_vendor: Optional[str]
    amc_expiry: Optional[date]
    last_service: Optional[date]
    pm_interval_days: int
    next_pm_due: Optional[date] = None
    days_to_pm: Optional[int] = None
    amc_days_left: Optional[int] = None
    pm_state: str = "OK"       # OK | DUE_SOON | OVERDUE
    amc_state: str = "NONE"    # NONE | OK | EXPIRING | EXPIRED
    open_tickets: int = 0


class ServiceDone(BaseModel):
    service_date: Optional[date] = None
    notes: Optional[str] = None


class TicketIn(BaseModel):
    equipment_id: Optional[int] = None
    category: TicketCategory
    title: str = Field(min_length=3, max_length=120)
    description: Optional[str] = None
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    parts_required: bool = True
    reported_by: Optional[str] = None


class TicketAdvance(BaseModel):
    to_status: TicketStatus
    technician_name: Optional[str] = None
    technician_phone: Optional[str] = None
    parts_replaced: Optional[str] = None
    note: Optional[str] = None


class TicketEventOut(ORM):
    ts: datetime
    from_status: Optional[str]
    to_status: str
    note: Optional[str]


class TicketOut(ORM):
    id: int
    ticket_no: str
    equipment_id: Optional[int]
    equipment_name: Optional[str] = None
    category: str
    title: str
    description: Optional[str]
    priority: str
    status: str
    parts_required: bool
    reported_by: Optional[str]
    technician_name: Optional[str]
    technician_phone: Optional[str]
    parts_replaced: Optional[str]
    resolution_notes: Optional[str]
    opened_at: datetime
    resolved_at: Optional[datetime]
    age_hours: float = 0
    events: list[TicketEventOut] = []


# ───────────── Compliance ─────────────
class ComplianceIn(BaseModel):
    doc_type: DocType
    title: str = Field(min_length=3, max_length=80)
    number: Optional[str] = None
    authority: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: date
    notes: Optional[str] = None


class ComplianceRenew(BaseModel):
    expiry_date: date
    number: Optional[str] = None
    issue_date: Optional[date] = None


class ComplianceOut(ORM):
    id: int
    doc_type: str
    title: str
    number: Optional[str]
    authority: Optional[str]
    issue_date: Optional[date]
    expiry_date: date
    notes: Optional[str]
    days_left: int = 0
    status: Literal["GREEN", "YELLOW", "RED"] = "GREEN"


# ───────────── Dashboard / alerts ─────────────
class AlertOut(BaseModel):
    severity: Literal["info", "warning", "critical"]
    category: str
    title: str
    detail: str
    ts: Optional[datetime] = None


class CreditCustomerOut(BaseModel):
    customer_name: str
    outstanding: float
    vouchers: int
    oldest: date


class DashboardOut(BaseModel):
    as_of: datetime
    day: date
    fuel_sold: dict[str, float]
    total_fuel_litres: float
    cng_kg: float
    expected_revenue: float
    gross_collections: float
    cash: float
    upi: float
    card: float
    credit: float
    cash_pct: float
    digital_pct: float
    variance_today: float
    outstanding_receivables: float
    active_staff: list[dict]
    active_staff_count: int
    open_tickets: int
    critical_tickets: int
    open_shift: Optional[dict]
    alert_count: int
    last7: list[dict]
    tank_status: list[dict]
