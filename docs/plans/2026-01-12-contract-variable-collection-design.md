# Contract Variable Collection System Design

## Overview

Replace the simple employee data collection in `development.ipynb` with a full contract variable collection system supporting 5 contract versions.

## Contract Versions

| Version | Type | Key Variables |
|---------|------|---------------|
| A | New Employee | workload_percentage, annual_gross_salary |
| B | New Employee (fixed term) | end_date, workload_percentage, monthly_gross_salary |
| C | New Employee (hourly) | hourly_workload_per_month, hourly_salary |
| D | Existing Employee | workload_percentage, annual_gross_salary, original contract dates |
| A1 | Existing Employee (alt) | Same as D |

## Common Variables (All Versions)

- full_name
- gender
- job_title
- start_date
- contract_signing_date
- company_signatory

## Architecture

### Workflow Structure (unchanged)

```
START → chatbot → (tools) → human_verification → create_entry → END
```

### State Definition

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    contract_version: str
    info_complete: bool
    human_decision: str

    # Common
    full_name: str
    gender: str
    job_title: str
    start_date: str
    contract_signing_date: str
    company_signatory: str

    # Version-specific
    end_date: str | None
    workload_percentage: float | None
    annual_gross_salary: float | None
    monthly_gross_salary: float | None
    hourly_salary: float | None
    hourly_workload_per_month: float | None
    original_contract_starting_date: str | None
    original_contract_signing_date: str | None

    # Calculated
    weekly_working_hours: float | None
```

## Calculation Formulas

- `weekly_working_hours = workload_percentage × 42`
- `hourly_workload_per_month = (weekly_working_hours × 52) ÷ 12`
- `monthly_gross_salary = annual_gross_salary ÷ 12`
- `annual_gross_salary = monthly_gross_salary × 12`
- `hourly_salary = monthly_gross_salary ÷ hourly_workload_per_month`

## Validation

### Required by Version

```python
REQUIRED_BY_VERSION = {
    "A":  ["workload_percentage", "annual_gross_salary"],
    "B":  ["end_date", "workload_percentage", "monthly_gross_salary"],
    "C":  ["hourly_workload_per_month", "hourly_salary"],
    "D":  ["workload_percentage", "annual_gross_salary",
           "original_contract_starting_date", "original_contract_signing_date"],
    "A1": ["workload_percentage", "annual_gross_salary",
           "original_contract_starting_date", "original_contract_signing_date"],
}
```

## Implementation Order

1. Update `development.ipynb` with full implementation
2. Test and iterate
3. Extract to `contract_collector.py` module

## Files

- `development.ipynb` - Interactive notebook (update)
- `contract_collector.py` - Importable module (create after notebook)
- `prompts/contract_chatbot_system_prompt.md` - System prompt (already exists)
