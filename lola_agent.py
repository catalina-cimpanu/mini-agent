#!/usr/bin/env python3
"""
Lola - HR Contract Data Collection Agent
Production-ready workflow for collecting employment contract information.
"""

import os
import json
import uuid
import re
import calendar
from typing import Literal, TypedDict, Annotated
from datetime import datetime
from dotenv import load_dotenv
from dateutil.relativedelta import relativedelta
from dateutil.parser import parse as dateutil_parse

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, AnyMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode

# =============================================================================
# CONFIGURATION
# =============================================================================

load_dotenv()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

EXIT_KEYWORDS = {"exit", "bye", "quit", "stop", "cancel", "goodbye", "end"}

WEEKDAY_MAP = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

REQUIRED_BY_VERSION = {
    "A":  ["workload_percentage", "annual_gross_salary"],
    "B":  ["end_date", "workload_percentage", "monthly_gross_salary"],
    "C":  ["hourly_workload_per_month", "hourly_salary"],
    "D":  ["workload_percentage", "annual_gross_salary",
           "original_contract_starting_date", "original_contract_signing_date"],
    "A1": ["workload_percentage", "annual_gross_salary",
           "original_contract_starting_date", "original_contract_signing_date"],
}

COMMON_REQUIRED = [
    "contract_version",
    "full_name", "gender", "job_title", "start_date",
    "contract_signing_date", "company_representative", "worker_representative"
]

AUTHORIZED_SIGNATORIES = [
    "Matthias Pfister",
    "Louisa Hugenschmidt",
    "Michael Grass",
    "Claude Maurer",
    "Diana Trogrlić",
]

VERSION_NAMES = {
    "A": "New Employee (Standard)",
    "B": "New Employee (Fixed Term)",
    "C": "New Employee (Hourly Rate)",
    "D": "Existing Employee (Amendment)",
    "A1": "Existing Employee (Amendment Alt.)",
}

# =============================================================================
# TOOLS
# =============================================================================

def get_weekday_from_text(text: str) -> int | None:
    text = text.lower().strip()
    for day_name, day_num in WEEKDAY_MAP.items():
        if day_name in text:
            return day_num
    return None

@tool
def get_current_datetime() -> str:
    """Get the current date and time. Use this to know what today's date is."""
    now = datetime.now()
    day_name = WEEKDAY_NAMES[now.weekday()]
    return f"TODAY IS: {day_name}, {now.strftime('%Y-%m-%d')}. Current time: {now.strftime('%H:%M:%S')}"

@tool
def parse_relative_date(date_expression: str) -> str:
    """
    Parse a relative date expression and return the resolved date.

    IMPORTANT: Always use this tool when user mentions ANY relative date like:
    - "today", "tomorrow", "yesterday"
    - "next Monday", "this Friday", "last Tuesday"
    - "in 3 days", "in 2 weeks", "in 1 month"
    - "next week", "next month"

    The tool returns the exact date - USE THIS DATE, do not calculate dates yourself!
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    current_weekday = today.weekday()
    today_str = today.strftime("%Y-%m-%d")
    today_day_name = WEEKDAY_NAMES[current_weekday]

    expr = date_expression.lower().strip()
    result_date = None

    if expr in ["today", "now"]:
        result_date = today
    elif expr == "tomorrow":
        result_date = today + relativedelta(days=1)
    elif expr == "yesterday":
        result_date = today - relativedelta(days=1)
    elif re.search(r'\d+\s*(day|week|month|year)', expr):
        match = re.search(r'(\d+)\s*(day|week|month|year)s?', expr)
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            if unit == "day":
                result_date = today + relativedelta(days=num)
            elif unit == "week":
                result_date = today + relativedelta(weeks=num)
            elif unit == "month":
                result_date = today + relativedelta(months=num)
            elif unit == "year":
                result_date = today + relativedelta(years=num)
    elif expr == "next week":
        days_until_monday = (7 - current_weekday) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        result_date = today + relativedelta(days=days_until_monday)
    elif expr == "next month":
        result_date = (today + relativedelta(months=1)).replace(day=1)
    elif expr == "next year":
        result_date = today.replace(year=today.year + 1, month=1, day=1)
    elif "end of month" in expr or "month end" in expr:
        last_day = calendar.monthrange(today.year, today.month)[1]
        result_date = today.replace(day=last_day)
    elif "end of year" in expr or "year end" in expr:
        result_date = today.replace(month=12, day=31)
    elif expr.startswith("this "):
        target_weekday = get_weekday_from_text(expr)
        if target_weekday is not None:
            days_diff = target_weekday - current_weekday
            result_date = today + relativedelta(days=days_diff)
    elif expr.startswith("next "):
        target_weekday = get_weekday_from_text(expr)
        if target_weekday is not None:
            days_diff = target_weekday - current_weekday
            if days_diff <= 0:
                days_diff += 7
            result_date = today + relativedelta(days=days_diff)
    elif expr.startswith("last "):
        target_weekday = get_weekday_from_text(expr)
        if target_weekday is not None:
            days_diff = current_weekday - target_weekday
            if days_diff <= 0:
                days_diff += 7
            result_date = today - relativedelta(days=days_diff)
    else:
        target_weekday = get_weekday_from_text(expr)
        if target_weekday is not None:
            days_diff = target_weekday - current_weekday
            if days_diff <= 0:
                days_diff += 7
            result_date = today + relativedelta(days=days_diff)

    if result_date is None:
        try:
            result_date = dateutil_parse(date_expression, fuzzy=True, default=today)
        except (ValueError, TypeError):
            return f"ERROR: Could not parse '{date_expression}'. Today is {today_day_name}, {today_str}. Try: 'next Monday', 'in 2 weeks', 'tomorrow'."

    result_day_name = WEEKDAY_NAMES[result_date.weekday()]
    result_str = result_date.strftime("%Y-%m-%d")

    days_from_today = (result_date - today).days
    if days_from_today == 0:
        days_context = "(today)"
    elif days_from_today == 1:
        days_context = "(tomorrow)"
    elif days_from_today == -1:
        days_context = "(yesterday)"
    elif days_from_today > 0:
        days_context = f"({days_from_today} days from today)"
    else:
        days_context = f"({abs(days_from_today)} days ago)"

    return f"RESOLVED DATE: {result_day_name}, {result_str} {days_context}. (Today is {today_day_name}, {today_str})"



# =============================================================================
# STATE
# =============================================================================

class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    contract_version: str
    info_complete: bool
    human_decision: str
    full_name: str
    gender: str
    job_title: str
    start_date: str
    contract_signing_date: str
    company_representative: str
    worker_representative: str
    end_date: str
    workload_percentage: float
    annual_gross_salary: float
    monthly_gross_salary: float
    hourly_salary: float
    hourly_workload_per_month: float
    original_contract_starting_date: str
    original_contract_signing_date: str
    weekly_working_hours: float
    contract_json: dict


# =============================================================================
# CALCULATION FUNCTIONS
# =============================================================================

def calculate_weekly_working_hours(workload_percentage: float) -> float:
    return (workload_percentage / 100) * 42


def calculate_hourly_workload_per_month(weekly_hours: float) -> float:
    return (weekly_hours * 52) / 12


def calculate_monthly_from_annual(annual_salary: float) -> float:
    return annual_salary / 12


def calculate_annual_from_monthly(monthly_salary: float) -> float:
    return monthly_salary * 12


def calculate_hourly_salary(monthly_salary: float, hourly_workload: float) -> float:
    if hourly_workload == 0:
        return 0.0
    return monthly_salary / hourly_workload


def calculate_all_values(data: dict) -> dict:
    version = data.get("contract_version", "")
    if not version:
        return data

    result = data.copy()
    workload = data.get("workload_percentage")

    if version == "C" and not workload:
        hourly_workload = data.get("hourly_workload_per_month", 0)
        if hourly_workload > 0:
            workload = (hourly_workload * 12) / (42 * 52) * 100
            result["workload_percentage"] = round(workload, 2)

    if workload:
        weekly_hours = calculate_weekly_working_hours(workload)
        result["weekly_working_hours"] = round(weekly_hours, 2)

        if version != "C" or not data.get("hourly_workload_per_month"):
            hourly_workload = calculate_hourly_workload_per_month(weekly_hours)
            result["hourly_workload_per_month"] = round(hourly_workload, 2)

    if version in ["A", "D", "A1"]:
        annual = data.get("annual_gross_salary", 0)
        if annual:
            monthly = calculate_monthly_from_annual(annual)
            result["monthly_gross_salary"] = round(monthly, 2)
            hourly_workload = result.get("hourly_workload_per_month", 0)
            if hourly_workload:
                result["hourly_salary"] = round(calculate_hourly_salary(monthly, hourly_workload), 2)
    elif version == "B":
        monthly = data.get("monthly_gross_salary", 0)
        if monthly:
            result["annual_gross_salary"] = round(calculate_annual_from_monthly(monthly), 2)
            hourly_workload = result.get("hourly_workload_per_month", 0)
            if hourly_workload:
                result["hourly_salary"] = round(calculate_hourly_salary(monthly, hourly_workload), 2)
    elif version == "C":
        hourly = data.get("hourly_salary", 0)
        hourly_workload = data.get("hourly_workload_per_month", 0)
        if hourly and hourly_workload:
            monthly = hourly * hourly_workload
            result["monthly_gross_salary"] = round(monthly, 2)
            result["annual_gross_salary"] = round(calculate_annual_from_monthly(monthly), 2)

    return result


# =============================================================================
# VALIDATION
# =============================================================================

def validate_contract_data(data: dict) -> list[str]:
    errors = []
    version = data.get("contract_version", "")

    if not version or version not in REQUIRED_BY_VERSION:
        errors.append(f"Contract version must be explicitly provided. Got: '{version}'. Must be A, B, C, D, or A1.")
        return errors

    for field in COMMON_REQUIRED:
        if field == "contract_version":
            continue
        value = data.get(field)
        if not value or (isinstance(value, str) and not value.strip()):
            errors.append(f"Missing required field: {field}")

    for field in REQUIRED_BY_VERSION[version]:
        value = data.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"Missing required field for Version {version}: {field}")

    gender = data.get("gender", "").lower()
    if gender and gender not in ["male", "female"]:
        errors.append(f"Gender must be 'male' or 'female', got: '{gender}'")

    company_rep = data.get("company_representative", "")
    worker_rep = data.get("worker_representative", "")

    if company_rep:
        is_authorized = any(name.lower() in company_rep.lower() for name in AUTHORIZED_SIGNATORIES)
        if not is_authorized:
            errors.append(f"Unauthorized company representative: '{company_rep}'. Must be one of: {', '.join(AUTHORIZED_SIGNATORIES)}")

    if worker_rep:
        is_authorized = any(name.lower() in worker_rep.lower() for name in AUTHORIZED_SIGNATORIES)
        if not is_authorized:
            errors.append(f"Unauthorized worker representative: '{worker_rep}'. Must be one of: {', '.join(AUTHORIZED_SIGNATORIES)}")

    workload = data.get("workload_percentage")
    if workload is not None:
        if workload < 1 or workload > 100:
            errors.append(f"Workload percentage must be 1-100%, got: {workload}%")

    for salary_field in ["annual_gross_salary", "monthly_gross_salary", "hourly_salary"]:
        salary = data.get(salary_field)
        if salary is not None and salary <= 0:
            errors.append(f"{salary_field} must be positive, got: {salary}")

    start_date = data.get("start_date", "")
    end_date = data.get("end_date")
    signing_date = data.get("contract_signing_date", "")

    if signing_date and start_date:
        if signing_date > start_date:
            errors.append(f"Contract signing date ({signing_date}) must be on or before start date ({start_date})")

    if version == "B" and end_date and start_date:
        if end_date <= start_date:
            errors.append(f"End date ({end_date}) must be after start date ({start_date})")

    if version in ["D", "A1"]:
        orig_start = data.get("original_contract_starting_date", "")
        if orig_start and start_date and orig_start > start_date:
            errors.append(f"Original contract start ({orig_start}) should be before new start ({start_date})")

    return errors


# =============================================================================
# JSON EXTRACTION
# =============================================================================

def extract_json_from_text(text: str) -> dict | None:
    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*"complete"\s*:\s*true[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'

    match = re.search(json_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    try:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            potential_json = text[start:end+1]
            data = json.loads(potential_json)
            if data.get("complete"):
                return data
    except json.JSONDecodeError:
        pass

    return None


# =============================================================================
# NODES
# =============================================================================

def chatbot(state: State, llm_with_tools, contract_prompt: str) -> dict:
    messages = state.get("messages", [])

    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            if msg.content.lower().strip() in EXIT_KEYWORDS:
                print("Conversation ended.")
                return {"info_complete": True, "human_decision": "cancel"}
            break

    llm_messages = [SystemMessage(content=contract_prompt)] + messages

    if not messages:
        llm_messages.append(HumanMessage(content="Hi, I need to create an employment contract."))

    try:
        response = llm_with_tools.invoke(llm_messages)
    except Exception as e:
        print(f"Error calling LLM: {e}")
        return {"info_complete": True, "human_decision": "cancel"}

    if response.tool_calls:
        return {"messages": [response]}

    assistant_message = response.content
    print(f"🤖 Lola: {assistant_message}")

    data = extract_json_from_text(assistant_message)

    if data and data.get("complete"):
        data = calculate_all_values(data)
        errors = validate_contract_data(data)

        if errors:
            error_msg = "⚠️ Validation errors:\n" + "\n".join(f"  - {e}" for e in errors)
            print(error_msg)
            user_input = input("You: ")
            return {
                "messages": [response, AIMessage(content=error_msg), HumanMessage(content=user_input)],
                "info_complete": False,
            }

        print("✅ Contract data collected and validated")
        version = data.get("contract_version")

        return {
            "messages": [response],
            "info_complete": True,
            "contract_version": version,
            "full_name": data.get("full_name", ""),
            "gender": data.get("gender", ""),
            "job_title": data.get("job_title", ""),
            "start_date": data.get("start_date", ""),
            "contract_signing_date": data.get("contract_signing_date", ""),
            "company_representative": data.get("company_representative", ""),
            "worker_representative": data.get("worker_representative", ""),
            "end_date": data.get("end_date"),
            "workload_percentage": data.get("workload_percentage"),
            "annual_gross_salary": data.get("annual_gross_salary"),
            "monthly_gross_salary": data.get("monthly_gross_salary"),
            "hourly_salary": data.get("hourly_salary"),
            "hourly_workload_per_month": data.get("hourly_workload_per_month"),
            "original_contract_starting_date": data.get("original_contract_starting_date"),
            "original_contract_signing_date": data.get("original_contract_signing_date"),
            "weekly_working_hours": data.get("weekly_working_hours"),
        }

    user_input = input("You: ")

    return {
        "messages": [response, HumanMessage(content=user_input)],
        "info_complete": False,
    }


def human_verification(state: State) -> dict:
    version = state.get("contract_version")

    print("\n" + "=" * 60)
    print("📋 CONTRACT DATA REVIEW")
    print("=" * 60)
    print(f"\n📄 CONTRACT TYPE: Version {version} - {VERSION_NAMES.get(version, 'Unknown')}")
    print(f"\n👤 EMPLOYEE:")
    print(f"   Name: {state.get('full_name')}")
    print(f"   Gender: {state.get('gender')}")
    print(f"   Title: {state.get('job_title')}")
    print(f"\n📅 DATES:")
    print(f"   Start: {state.get('start_date')}")
    if version == "B":
        print(f"   End: {state.get('end_date')}")
    print(f"   Signing: {state.get('contract_signing_date')}")

    if version in ["D", "A1"]:
        print(f"\n📜 ORIGINAL CONTRACT:")
        print(f"   Start: {state.get('original_contract_starting_date')}")
        print(f"   Signing: {state.get('original_contract_signing_date')}")

    print(f"\n⏱️ WORKLOAD:")
    print(f"   {state.get('workload_percentage')}% ({state.get('weekly_working_hours')} hrs/week)")

    print(f"\n💰 SALARY (CHF):")
    if state.get('annual_gross_salary'):
        print(f"   Annual: {state.get('annual_gross_salary'):,.2f}")
    if state.get('monthly_gross_salary'):
        print(f"   Monthly: {state.get('monthly_gross_salary'):,.2f}")

    print(f"\n✍️ SIGNATORIES:")
    print(f"   Company: {state.get('company_representative')}")
    print(f"   Worker: {state.get('worker_representative')}")
    print("\n" + "=" * 60)

    decision = input("\nApprove? (yes/no): ").lower().strip()

    if decision in ["yes", "y", "approve"]:
        print("✅ Approved")
        return {"human_decision": "approve"}
    else:
        correction = input("What needs correction? ")
        correction_message = HumanMessage(content=f"Correction needed: {correction}")
        return {
            "human_decision": "reject",
            "info_complete": False,
            "messages": [correction_message]
        }


def state_to_json(state: State) -> dict:
    version = state.get("contract_version")

    contract_data = {
        "contract_version": version,
        "full_name": state.get("full_name"),
        "gender": state.get("gender"),
        "job_title": state.get("job_title"),
        "start_date": state.get("start_date"),
        "contract_signing_date": state.get("contract_signing_date"),
        "company_representative": state.get("company_representative"),
        "worker_representative": state.get("worker_representative"),
        "workload_percentage": state.get("workload_percentage"),
        "weekly_working_hours": state.get("weekly_working_hours"),
        "hourly_workload_per_month": state.get("hourly_workload_per_month"),
        "annual_gross_salary": state.get("annual_gross_salary"),
        "monthly_gross_salary": state.get("monthly_gross_salary"),
        "hourly_salary": state.get("hourly_salary"),
    }

    if version == "B":
        contract_data["end_date"] = state.get("end_date")

    if version in ["D", "A1"]:
        contract_data["original_contract_starting_date"] = state.get("original_contract_starting_date")
        contract_data["original_contract_signing_date"] = state.get("original_contract_signing_date")

    return contract_data


def create_entry(state: State) -> dict:
    contract_json = state_to_json(state)

    print(f"\n✅ CONTRACT CREATED")
    print(f"   Version: {contract_json['contract_version']}")
    print(f"   Employee: {contract_json['full_name']}")
    print(f"   Start: {contract_json['start_date']}")
    print("\n" + json.dumps(contract_json, indent=2, ensure_ascii=False))

    return {"contract_json": contract_json}


def update_entry(state: State) -> dict:
    contract_json = state_to_json(state)

    print(f"\n✅ CONTRACT UPDATED")
    print(f"   Version: {contract_json['contract_version']}")
    print(f"   Employee: {contract_json['full_name']}")
    print("\n" + json.dumps(contract_json, indent=2, ensure_ascii=False))

    return {"contract_json": contract_json}


# =============================================================================
# ROUTERS
# =============================================================================

def route_after_chatbot(state: State) -> str:
    if state.get("human_decision") == "cancel":
        return END

    messages = state.get("messages", [])
    if messages:
        last_msg = messages[-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return "tools"

    if state.get("info_complete"):
        return "human_verification"

    return "chatbot"


def route_after_verification(state: State) -> str:
    decision = state.get("human_decision")
    version = state.get("contract_version")

    if decision == "reject":
        return "chatbot"

    if decision == "approve":
        if version in ["D", "A1"]:
            return "update_entry"
        return "create_entry"

    return "chatbot"


# =============================================================================
# MAIN
# =============================================================================

def get_llm(provider: str):
    if provider == "anthropic":
        return ChatAnthropic(model=ANTHROPIC_MODEL, temperature=0.7, max_tokens=1024)
    elif provider == "openai":
        return ChatOpenAI(model=OPENAI_MODEL, temperature=0.7, max_tokens=1024)
    else:
        raise ValueError(f"Unknown provider: {provider}")


def load_system_prompt(filename: str) -> str:
    try:
        with open(f"prompts/{filename}.md", 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"Error loading system prompt: {e}")
        raise


def build_workflow(llm_with_tools, tool_node, contract_prompt):
    def chatbot_wrapper(state: State) -> dict:
        return chatbot(state, llm_with_tools, contract_prompt)

    workflow = StateGraph(State)
    workflow.add_node("chatbot", chatbot_wrapper)
    workflow.add_node("tools", tool_node)
    workflow.add_node("human_verification", human_verification)
    workflow.add_node("create_entry", create_entry)
    workflow.add_node("update_entry", update_entry)

    workflow.add_edge(START, "chatbot")
    workflow.add_conditional_edges("chatbot", route_after_chatbot, ["chatbot", "tools", "human_verification", END])
    workflow.add_edge("tools", "chatbot")
    workflow.add_conditional_edges("human_verification", route_after_verification, ["chatbot", "create_entry", "update_entry"])
    workflow.add_edge("create_entry", END)
    workflow.add_edge("update_entry", END)

    return workflow


def get_initial_state():
    return {
        "messages": [],
        "contract_version": "",
        "info_complete": False,
        "human_decision": "",
        "full_name": "",
        "gender": "",
        "job_title": "",
        "start_date": "",
        "contract_signing_date": "",
        "company_representative": "",
        "worker_representative": "",
        "end_date": None,
        "workload_percentage": None,
        "annual_gross_salary": None,
        "monthly_gross_salary": None,
        "hourly_salary": None,
        "hourly_workload_per_month": None,
        "original_contract_starting_date": None,
        "original_contract_signing_date": None,
        "weekly_working_hours": None,
        "contract_json": None,
    }


def main():
    try:
        print("=" * 50)
        print("CONTRACT DATA COLLECTION")
        print("=" * 50)

        llm = get_llm(LLM_PROVIDER)
        tools = [parse_relative_date, get_current_datetime]
        llm_with_tools = llm.bind_tools(tools)
        tool_node = ToolNode(tools)
        contract_prompt = load_system_prompt("contract_chatbot_system_prompt")

        workflow = build_workflow(llm_with_tools, tool_node, contract_prompt)
        memory = MemorySaver()
        app = workflow.compile(checkpointer=memory)

        thread_id = f"contract-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

        print(f"\n🚀 Starting conversation\n")

        for event in app.stream(get_initial_state(), config):
            pass

        final_state = app.get_state(config)

        if final_state and final_state.values:
            contract_result = final_state.values.get("contract_json")
            if contract_result:
                print("\n✅ Workflow complete")

    except KeyboardInterrupt:
        print("\n\nWorkflow interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise


if __name__ == "__main__":
    main()
