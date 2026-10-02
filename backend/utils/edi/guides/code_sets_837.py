"""Code value meanings for HIPAA 837 (005010X222/X223/X224) — companion guide tables."""

from __future__ import annotations

# Segment / element keyed code lists (ST01-style IDs in comments where helpful).

NM101_ENTITY = {
    "41": "Submitter",
    "40": "Receiver",
    "85": "Billing Provider",
    "87": "Pay-to Provider",
    "PE": "Payee",
    "IL": "Insured or Subscriber",
    "PR": "Payer",
    "QC": "Patient",
    "82": "Rendering Provider",
    "77": "Service Location",
    "DN": "Referring Provider",
    "DK": "Ordering Provider",
    "DQ": "Supervising Provider",
    "PW": "Ambulance Pick-up Location",
    "45": "Ambulance Drop-off Location",
}

NM102_ENTITY_TYPE = {
    "1": "Person",
    "2": "Non-Person Entity",
}

NM108_ID_QUAL = {
    "46": "Electronic Transmitter Identification Number (ETIN)",
    "XX": "Health Care Financing Administration National Provider Identifier",
    "MI": "Member Identification Number",
    "PI": "Payor Identification",
    "XV": "Health Care Financing Administration PlanID",
    "SY": "Social Security Number",
    "EI": "Employer's Identification Number",
    "0B": "State License Number",
    "G2": "Provider Commercial Number",
    "PXC": "Health Care Provider Taxonomy Code",
}

ISA01_AUTH = {"00": "No Authorization Information Present", "03": "Additional Data Identification"}

ISA03_SEC = {"00": "No Security Information Present", "01": "Password"}

ISA05_07_ID_QUAL = {
    "01": "Duns (Dun & Bradstreet)",
    "14": "Duns Plus Suffix",
    "20": "Health Industry Number (HIN)",
    "27": "Carrier Identification Number (U.S.)",
    "28": "Fiscal Intermediary Identification Number",
    "29": "Medicare Provider and Supplier Identification Number",
    "30": "U.S. Federal Tax Identification Number",
    "33": "National Association of Insurance Commissioners (NAIC)",
    "ZZ": "Mutually Defined",
}

ISA11_STD = {"U": "U.S. EDI Community of ASC X12, TDCC, and UCS", "^": "Represents repetition separator in ISA16"}

ISA14_ACK = {"0": "No Interchange Acknowledgment Requested", "1": "Interchange Acknowledgment Requested"}

ISA15_USAGE = {"P": "Production Data", "T": "Test Data"}

GS01_FUNC = {
    "HC": "Health Care Claim (837)",
    "HP": "Health Care Claim Payment/Advice (835)",
    "HS": "Eligibility Inquiry (270)",
    "HB": "Eligibility Response (271)",
}

GS08_VERSION = {
    "005010X222A1": "Health Care Claim: Professional (837P) — guide 005010X222A1",
    "005010X222A2": "Health Care Claim: Professional (837P) — guide 005010X222A2",
    "005010X223A2": "Health Care Claim: Institutional (837I) — guide 005010X223A2",
    "005010X224A2": "Health Care Claim: Dental (837D) — guide 005010X224A2",
}

BHT02_PURPOSE = {
    "00": "Original",
    "18": "Reissue",
}

BHT06_TYPE = {
    "CH": "Chargeable",
    "RP": "Reporting",
    "31": "Subrogation Demand",
}

HL03_LEVEL = {
    "20": "Information Source",
    "22": "Subscriber Level",
    "23": "Dependent Level",
}

HL04_CHILD = {
    "0": "No Subordinate HL Segment in This Hierarchical Structure",
    "1": "Additional Subordinate HL Data Segment in This Hierarchical Structure",
}

SBR01_PAYER = {
    "P": "Primary",
    "S": "Secondary",
    "T": "Tertiary",
    "A": "Payer Responsibility Four",
    "B": "Payer Responsibility Five",
    "C": "Payer Responsibility Six",
    "D": "Payer Responsibility Seven",
    "E": "Payer Responsibility Eight",
    "F": "Payer Responsibility Nine",
    "G": "Payer Responsibility Ten",
    "U": "Unknown",
    "W": "Workers Compensation",
}

SBR09_CLAIM_FILING = {
    "MB": "Medicare Part B",
    "MC": "Medicaid",
    "CI": "Commercial Insurance Co.",
    "HM": "Health Maintenance Organization (HMO)",
    "BL": "Blue Cross/Blue Shield",
    "WC": "Workers Compensation",
    "ZZ": "Mutually Defined",
}

DMG01_FORMAT = {"D8": "Date Expressed in Format CCYYMMDD"}

DMG03_GENDER = {"M": "Male", "F": "Female", "U": "Unknown"}

DTP_FORMAT = {
    "D8": "Date Expressed in Format CCYYMMDD",
    "RD8": "Range of Dates Expressed in Format CCYYMMDD-CCYYMMDD",
    "TM": "Time Expressed in Format HHMM",
}

DTP01_QUAL = {
    "431": "Onset of Current Symptoms or Illness",
    "454": "Initial Treatment",
    "304": "Latest Visit or Consultation",
    "453": "Acute Manifestation of a Chronic Condition",
    "439": "Accident",
    "484": "Last Menstrual Period",
    "455": "Last X-ray",
    "471": "Prescription",
    "314": "Disability Period Start",
    "435": "Admission",
    "096": "Discharge",
    "090": "Report Start",
    "091": "Report End",
    "050": "Received",
    "472": "Service",
    "573": "Date Claim Paid",
}

REF01_QUAL = {
    "EI": "Employer's Identification Number",
    "SY": "Social Security Number",
    "0B": "State License Number",
    "G2": "Provider Commercial Number",
    "FY": "Claim Office Number",
    "6R": "Provider Control Number",
    "4N": "Special Payment Reference Number",
    "F5": "Medicare Version Code",
    "EW": "Mammography Certification Number",
    "9F": "Referral Number",
    "G1": "Prior Authorization Number",
    "F8": "Original Reference Number",
    "X4": "Clinical Laboratory Improvement Amendment Number",
    "9A": "Repriced Claim Reference Number",
    "9C": "Adjusted Repriced Claim Reference Number",
    "LX": "Investigational Device Exemption Number",
    "D9": "Claim Identifier for Transmission Intermediaries",
    "EA": "Medical Record Identification Number",
    "P4": "Demonstration Project Identifier",
    "1J": "Care Plan Oversight",
}

CLM05_FACILITY = {
    "11": "Office",
    "12": "Home",
    "21": "Inpatient Hospital",
    "22": "Outpatient Hospital",
    "23": "Emergency Room — Hospital",
    "31": "Skilled Nursing Facility",
    "32": "Nursing Facility",
    "81": "Independent Laboratory",
}

CLM06_PROV_SIG = {"Y": "Yes", "N": "No"}

CLM07_ASSIGNMENT = {"A": "Assigned", "B": "Assignment Accepted", "C": "Not Assigned"}

HI01_QUAL = {
    "ABK": "Principal Diagnosis — ICD-10-CM (companion guide: use when service date is 10/01/2015 and after)",
    "ABF": "Additional Diagnosis — ICD-10-CM (companion guide: use when service date is 10/01/2015 and after)",
    "BK": "Principal Diagnosis — ICD-9-CM (service date 9/30/2015 and prior)",
    "BF": "Additional Diagnosis — ICD-9-CM (service date 9/30/2015 and prior)",
}

SV101_PRODUCT = {
    "HC": "Health Care Financing Administration Common Procedural Coding System (HCPCS)",
    "IV": "Home Infusion EDI Coalition (HIEC) Product/Service Code",
    "ER": "Jurisdiction Specific Procedure and Supply Codes",
    "WK": "Advanced Billing Concepts (ABC) Codes",
}

PER01_FUNCTION = {
    "IC": "Information Contact",
    "IP": "Insured Party",
    "CX": "Payers Claim Office",
}

PER03_COMM = {
    "TE": "Telephone",
    "EX": "Telephone Extension",
    "EM": "Electronic Mail",
    "FX": "Facsimile",
}

PRV01_PROVIDER = {"BI": "Billing", "PE": "Performing", "RF": "Referring", "AT": "Attending"}

PRV02_QUAL = {"PXC": "Health Care Provider Taxonomy Code"}

PAT01_RELATION = {
    "19": "Child",
    "01": "Spouse",
    "20": "Employee",
    "21": "Unknown",
    "39": "Organ Donor",
    "40": "Cadaver Donor",
    "53": "Life Partner",
    "G8": "Other Relationship",
}

AMT01_QUAL = {
    "F5": "Patient Amount Paid",
    "D": "Payor Amount Paid",
    "A8": "Non-covered Charges — Actual",
}

PWK01_REPORT = {
    "OZ": "Support Data for Claim",
    "BM": "Attachment — Binary",
}

PWK02_TRANS = {
    "BM": "By Mail",
    "EL": "Electronically Only",
    "FX": "By Fax",
}

CRC01_CATEGORY = {
    "7": "Ambulance Certification",
    "E1": "Condition Indicator / Durable Medical Equipment",
}

OI03_BENEFITS = {"Y": "Yes", "N": "No", "W": "Not Applicable"}

CAS01_GROUP = {
    "CO": "Contractual Obligations",
    "CR": "Correction and Reversals",
    "OA": "Other Adjustments",
    "PI": "Payor Initiated Reductions",
    "PR": "Patient Responsibility",
}

CODE_SETS: dict[str, dict[str, str]] = {
    "NM101": NM101_ENTITY,
    "NM102": NM102_ENTITY_TYPE,
    "NM108": NM108_ID_QUAL,
    "ISA01": ISA01_AUTH,
    "ISA03": ISA03_SEC,
    "ISA05": ISA05_07_ID_QUAL,
    "ISA07": ISA05_07_ID_QUAL,
    "ISA11": ISA11_STD,
    "ISA14": ISA14_ACK,
    "ISA15": ISA15_USAGE,
    "GS01": GS01_FUNC,
    "GS08": GS08_VERSION,
    "BHT02": BHT02_PURPOSE,
    "BHT06": BHT06_TYPE,
    "HL03": HL03_LEVEL,
    "HL04": HL04_CHILD,
    "SBR01": SBR01_PAYER,
    "SBR09": SBR09_CLAIM_FILING,
    "DMG01": DMG01_FORMAT,
    "DMG03": DMG03_GENDER,
    "DTP02": DTP_FORMAT,
    "DTP01": DTP01_QUAL,
    "REF01": REF01_QUAL,
    "CLM05_POS": CLM05_FACILITY,
    "CLM06": CLM06_PROV_SIG,
    "CLM07": CLM07_ASSIGNMENT,
    "CAS01": CAS01_GROUP,
    "HI01": HI01_QUAL,
    "SV101": SV101_PRODUCT,
    "PER01": PER01_FUNCTION,
    "PER03": PER03_COMM,
    "PER05": PER03_COMM,
    "PRV01": PRV01_PROVIDER,
    "PRV02": PRV02_QUAL,
    "PAT01": PAT01_RELATION,
    "AMT01": AMT01_QUAL,
    "PWK01": PWK01_REPORT,
    "PWK02": PWK02_TRANS,
    "CRC01": CRC01_CATEGORY,
    "OI03": OI03_BENEFITS,
}


def lookup_code(code_set_id: str, value: str) -> str | None:
    if not value:
        return None
    table = CODE_SETS.get(code_set_id)
    if not table:
        return None
    primary = value.split(":")[0].split("^")[0].strip()
    return table.get(primary)
