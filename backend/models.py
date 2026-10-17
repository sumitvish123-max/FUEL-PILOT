"""SQLAlchemy ORM models for FuelPilot."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, now_ist


# ───────────────────────── Staff & attendance ─────────────────────────
class Staff(Base):
    __tablename__ = "staff"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(16), unique=True, default="")
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(40))
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    shift_name: Mapped[str] = mapped_column(String(20), default="MORNING")
    shift_start: Mapped[str] = mapped_column(String(5), default="06:00")   # HH:MM
    shift_end: Mapped[str] = mapped_column(String(5), default="14:00")
    assigned_nozzles: Mapped[list] = mapped_column(JSON, default=list)      # ["D1-N1", ...]
    face_embeddings: Mapped[list] = mapped_column(JSON, default=list)       # list of 128-float lists
    face_engine: Mapped[str] = mapped_column(String(20), default="")
    biometric_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)

    @property
    def photo_count(self) -> int:
        return len(self.face_embeddings or [])


class Attendance(Base):
    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    in_time: Mapped[datetime] = mapped_column(DateTime)
    out_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default="PRESENT")      # PRESENT | LATE
    in_distance: Mapped[Optional[float]] = mapped_column(Float)
    out_distance: Mapped[Optional[float]] = mapped_column(Float)
    staff: Mapped[Staff] = relationship()

    @property
    def worked_minutes(self) -> Optional[int]:
        return int((self.out_time - self.in_time).total_seconds() // 60) if self.out_time else None


class FaceScanLog(Base):
    """Every scan attempt (success or failure) – the audit trail against proxy attendance."""
    __tablename__ = "face_scan_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)
    staff_id: Mapped[Optional[int]] = mapped_column(ForeignKey("staff.id"))
    success: Mapped[bool] = mapped_column(Boolean)
    distance: Mapped[Optional[float]] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(160), default="")


# ───────────────────────── Dispensers, rates, shifts ─────────────────────────
class FuelRate(Base):
    __tablename__ = "fuel_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    product: Mapped[str] = mapped_column(String(10), index=True)            # PETROL | DIESEL | CNG
    rate: Mapped[float] = mapped_column(Float)                              # ₹ per litre / kg
    effective_from: Mapped[datetime] = mapped_column(DateTime, default=now_ist)


class Nozzle(Base):
    __tablename__ = "nozzles"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True)              # D1-N1
    dispenser: Mapped[str] = mapped_column(String(30))
    product: Mapped[str] = mapped_column(String(10))
    unit: Mapped[str] = mapped_column(String(3), default="L")               # L | KG
    totaliser_max: Mapped[float] = mapped_column(Float, default=99_999_999.99)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Shift(Base):
    __tablename__ = "shifts"
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    shift_name: Mapped[str] = mapped_column(String(20))
    manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("staff.id"))
    status: Mapped[str] = mapped_column(String(8), default="OPEN", index=True)   # OPEN | CLOSED
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expected_revenue: Mapped[float] = mapped_column(Float, default=0)
    cash_amount: Mapped[float] = mapped_column(Float, default=0)
    upi_amount: Mapped[float] = mapped_column(Float, default=0)
    card_amount: Mapped[float] = mapped_column(Float, default=0)
    credit_amount: Mapped[float] = mapped_column(Float, default=0)          # fleet/credit vouchers
    total_collections: Mapped[float] = mapped_column(Float, default=0)
    variance: Mapped[float] = mapped_column(Float, default=0)               # collections − expected
    variance_status: Mapped[str] = mapped_column(String(8), default="")     # OK | SHORT | OVER
    severity: Mapped[str] = mapped_column(String(8), default="")            # ok | warning | critical
    tolerance: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    manager: Mapped[Optional[Staff]] = relationship()
    readings: Mapped[list["MeterReading"]] = relationship(back_populates="shift", cascade="all, delete-orphan")
    vouchers: Mapped[list["CreditVoucher"]] = relationship(back_populates="shift", cascade="all, delete-orphan")


class MeterReading(Base):
    """Totaliser (electronic meter) reading for one nozzle in one shift."""
    __tablename__ = "meter_readings"
    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), index=True)
    nozzle_id: Mapped[int] = mapped_column(ForeignKey("nozzles.id"))
    opening: Mapped[float] = mapped_column(Float)
    closing: Mapped[Optional[float]] = mapped_column(Float)
    test_litres: Mapped[float] = mapped_column(Float, default=0)            # returned to tank after 5 L test
    sold_qty: Mapped[float] = mapped_column(Float, default=0)               # (closing − opening) − test
    rate: Mapped[float] = mapped_column(Float, default=0)                   # rate snapshot at shift open
    expected_amount: Mapped[float] = mapped_column(Float, default=0)
    opening_mismatch: Mapped[bool] = mapped_column(Boolean, default=False) # opening ≠ previous closing
    shift: Mapped[Shift] = relationship(back_populates="readings")
    nozzle: Mapped[Nozzle] = relationship()


class CreditVoucher(Base):
    __tablename__ = "credit_vouchers"
    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), index=True)
    customer_name: Mapped[str] = mapped_column(String(80))
    vehicle_no: Mapped[Optional[str]] = mapped_column(String(20))
    voucher_no: Mapped[Optional[str]] = mapped_column(String(30))
    amount: Mapped[float] = mapped_column(Float)
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    shift: Mapped[Shift] = relationship(back_populates="vouchers")


# ───────────────────────── Tanks, dips, deliveries ─────────────────────────
class Tank(Base):
    __tablename__ = "tanks"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True)
    product: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(12), default="UNDERGROUND")    # UNDERGROUND | CASCADE
    capacity: Mapped[float] = mapped_column(Float)                          # litres (kg for CNG)
    diameter_mm: Mapped[float] = mapped_column(Float, default=2500)
    min_stock: Mapped[float] = mapped_column(Float, default=0)
    dead_stock: Mapped[float] = mapped_column(Float, default=0)


class DipReading(Base):
    __tablename__ = "dip_readings"
    id: Mapped[int] = mapped_column(primary_key=True)
    tank_id: Mapped[int] = mapped_column(ForeignKey("tanks.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)
    reading_type: Mapped[str] = mapped_column(String(14), default="ADHOC")  # OPENING|CLOSING|ADHOC
    dip_mm: Mapped[float] = mapped_column(Float, default=0)
    water_mm: Mapped[float] = mapped_column(Float, default=0)
    volume: Mapped[float] = mapped_column(Float)                            # computed from dip chart
    recorded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("staff.id"))
    shift_id: Mapped[Optional[int]] = mapped_column(ForeignKey("shifts.id"))
    tank: Mapped[Tank] = relationship()


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    tank_id: Mapped[int] = mapped_column(ForeignKey("tanks.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_ist, index=True)
    shift_id: Mapped[Optional[int]] = mapped_column(ForeignKey("shifts.id"))
    invoice_no: Mapped[str] = mapped_column(String(40))
    supplier: Mapped[Optional[str]] = mapped_column(String(60))
    vehicle_no: Mapped[Optional[str]] = mapped_column(String(20))
    invoiced_qty: Mapped[float] = mapped_column(Float)
    pre_dip_mm: Mapped[float] = mapped_column(Float)
    post_dip_mm: Mapped[float] = mapped_column(Float)
    pre_volume: Mapped[float] = mapped_column(Float)
    post_volume: Mapped[float] = mapped_column(Float)
    sales_during: Mapped[float] = mapped_column(Float, default=0)           # litres sold while unloading
    received_qty: Mapped[float] = mapped_column(Float)
    variance_qty: Mapped[float] = mapped_column(Float)                      # received − invoiced (−ve = short)
    variance_pct: Mapped[float] = mapped_column(Float)
    invoice_density: Mapped[Optional[float]] = mapped_column(Float)
    observed_density: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(14), default="OK")           # OK | SHORT | DENSITY_FLAG | SHORT_DENSITY
    notes: Mapped[Optional[str]] = mapped_column(Text)
    tank: Mapped[Tank] = relationship()


# ───────────────────────── Maintenance (CMMS) ─────────────────────────
class Equipment(Base):
    __tablename__ = "equipment"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(20))                       # DISPENSER|COMPRESSOR|ATG|OTHER
    status: Mapped[str] = mapped_column(String(12), default="OPERATIONAL")  # OPERATIONAL | WATCH | DOWN
    health: Mapped[int] = mapped_column(Integer, default=100)
    amc_vendor: Mapped[Optional[str]] = mapped_column(String(60))
    amc_expiry: Mapped[Optional[date]] = mapped_column(Date)
    last_service: Mapped[Optional[date]] = mapped_column(Date)
    pm_interval_days: Mapped[int] = mapped_column(Integer, default=90)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(14), unique=True, default="")
    equipment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("equipment.id"))
    category: Mapped[str] = mapped_column(String(24))                       # NOZZLE_LEAK, HOSE_BURST, ...
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(8), default="MEDIUM")      # LOW|MEDIUM|HIGH|CRITICAL
    status: Mapped[str] = mapped_column(String(22), default="OPEN", index=True)
    parts_required: Mapped[bool] = mapped_column(Boolean, default=True)
    reported_by: Mapped[Optional[str]] = mapped_column(String(60))
    technician_name: Mapped[Optional[str]] = mapped_column(String(60))
    technician_phone: Mapped[Optional[str]] = mapped_column(String(20))
    parts_replaced: Mapped[Optional[str]] = mapped_column(String(200))
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    equipment: Mapped[Optional[Equipment]] = relationship()
    events: Mapped[list["TicketEvent"]] = relationship(back_populates="ticket", cascade="all, delete-orphan",
                                                       order_by="TicketEvent.ts")


class TicketEvent(Base):
    __tablename__ = "ticket_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now_ist)
    from_status: Mapped[Optional[str]] = mapped_column(String(22))
    to_status: Mapped[str] = mapped_column(String(22))
    note: Mapped[Optional[str]] = mapped_column(Text)
    ticket: Mapped[Ticket] = relationship(back_populates="events")


# ───────────────────────── Compliance ─────────────────────────
class ComplianceDoc(Base):
    __tablename__ = "compliance_docs"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(24))        # WM_CALIBRATION|FIRE_NOC|EXPLOSIVES_LICENSE|POLLUTION|OTHER
    title: Mapped[str] = mapped_column(String(80))
    number: Mapped[Optional[str]] = mapped_column(String(60))
    authority: Mapped[Optional[str]] = mapped_column(String(80))
    issue_date: Mapped[Optional[date]] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
