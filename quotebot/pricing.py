"""Bambridge Renewables pricing engine.

Implements the agreed labour & scaffolding formula from
data/Bambridge_Pricing_Formula.xlsx ("Full Quick Calculator", rows 68-92).
Rates agreed June 2026 — review annually. If the spreadsheet rates change,
update RATES below to match.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

RATES = {
    "panel_labour": 80,          # per panel
    "dc_stringing": 25,          # per panel
    "battery_install": 220,      # per battery
    "battery_dc_bus": 100,       # per battery
    "frame_rail_per_face": 150,  # per roof face
    "string_to_inverter": 50,    # per DC string
    "inverter_mount_ac": 200,    # inverter wall mount + AC connection to CU
    "inverter_commissioning": 80,
    "ac_materials": 400,
    "g99_total": 300,            # admin + commissioning visit, systems >3.68kW
    "mcs_registration": 100,
    "ev_charger": 150,
}

SCAFFOLD = {
    "bungalow": 400,             # ground floor / bungalow
    "single_storey_extension": 500,
    "two_storey": 800,           # agreed rate, standard semi/detached
    "two_storey_complex": 1000,  # hipped/multi-plane or awkward access
    "three_storey": 1400,        # three storey / terrace
}

EXTRAS = {
    "new_consumer_unit": 400,
    "iwa_warranty": 60,
    "solar_breaker": 60,
    "wifi_monitoring": 50,
    "dc_cable_per_5m": 25,
    "carport_pergola": 350,
    "ground_mount_frame": 400,
    "flat_roof_ballast_per_face": 250,
    "building_regs": 50,
    "eess_record": 50,
    "smart_meter_ct": 60,
    "hybrid_inverter": 150,
}

G99_THRESHOLD_KW = 3.68


@dataclass
class JobSpec:
    panels: int
    batteries: int = 0
    roof_faces: int = 1
    dc_strings: int = 1
    scaffold: str = "two_storey"
    panel_wattage_kw: float = 0.44
    g99: Optional[bool] = None       # None = auto from system size
    ev_charger: bool = False
    extras: List[str] = field(default_factory=list)

    @property
    def system_kw(self) -> float:
        return round(self.panels * self.panel_wattage_kw, 2)

    @property
    def g99_required(self) -> bool:
        if self.g99 is not None:
            return self.g99
        return self.system_kw > G99_THRESHOLD_KW


def calculate(spec: JobSpec) -> "Quote":
    lines: List[Tuple[str, int]] = []

    def add(label: str, amount: int) -> None:
        if amount:
            lines.append((label, amount))

    add(f"Panel labour ({spec.panels} x £{RATES['panel_labour']})",
        spec.panels * RATES["panel_labour"])
    add(f"DC stringing ({spec.panels} x £{RATES['dc_stringing']})",
        spec.panels * RATES["dc_stringing"])
    add(f"Battery install ({spec.batteries} x £{RATES['battery_install']})",
        spec.batteries * RATES["battery_install"])
    add(f"Battery rack & DC bus ({spec.batteries} x £{RATES['battery_dc_bus']})",
        spec.batteries * RATES["battery_dc_bus"])
    add(f"Frame/rail install ({spec.roof_faces} face(s) x £{RATES['frame_rail_per_face']})",
        spec.roof_faces * RATES["frame_rail_per_face"])
    add(f"DC strings to inverter ({spec.dc_strings} x £{RATES['string_to_inverter']})",
        spec.dc_strings * RATES["string_to_inverter"])
    add("Inverter mount & AC connection", RATES["inverter_mount_ac"])
    add("Inverter commissioning & setup", RATES["inverter_commissioning"])
    add("AC electrical materials", RATES["ac_materials"])

    scaffold_key = spec.scaffold if spec.scaffold in SCAFFOLD else "two_storey"
    add(f"Scaffolding ({scaffold_key.replace('_', ' ')})", SCAFFOLD[scaffold_key])

    if spec.g99_required:
        add(f"G99 admin + commissioning (system {spec.system_kw}kW)",
            RATES["g99_total"])
    add("MCS registration", RATES["mcs_registration"])
    if spec.ev_charger:
        add("EV charger add-on", RATES["ev_charger"])
    for extra in spec.extras:
        if extra in EXTRAS:
            add(f"Extra: {extra.replace('_', ' ')}", EXTRAS[extra])

    return Quote(spec=spec, lines=lines, total=sum(a for _, a in lines))


@dataclass
class Quote:
    spec: JobSpec
    lines: List[Tuple[str, int]]
    total: int

    def as_text(self) -> str:
        out = ["BAMBRIDGE SERVICES QUOTE",
               f"System: {self.spec.panels} panels ({self.spec.system_kw}kW), "
               f"{self.spec.batteries} battery(ies)", "-" * 46]
        for label, amount in self.lines:
            out.append(f"{label:<38} £{amount:>6,}")
        out.append("-" * 46)
        out.append(f"{'TOTAL SERVICES COST':<38} £{self.total:>6,}")
        return "\n".join(out)


def scaffold_from_text(text: str) -> str:
    """Best-effort mapping of free text (e.g. 'two storey house') to a
    scaffold rate key."""
    t = (text or "").lower()
    if any(w in t for w in ("bungalow", "ground floor", "single storey", "1 storey", "one storey")):
        if "extension" in t:
            return "single_storey_extension"
        return "bungalow"
    if any(w in t for w in ("three", "3 storey", "3-storey", "terrace")):
        return "three_storey"
    if any(w in t for w in ("complex", "hipped", "hip", "awkward", "multi")):
        return "two_storey_complex"
    return "two_storey"
