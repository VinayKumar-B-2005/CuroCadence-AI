"""
CuroCadence AI — MCP Server
Exposes 6 medication management tools via MCP stdio transport.
"""

import json
import sys
import datetime
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types as mcp_types

# Import shared data store and tool functions from agent module
# (In production these would share a database; here we import the in-memory store)
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent import (
    get_medication_schedule,
    add_medication,
    check_interaction,
    log_dose_taken,
    export_schedule_ics,
    get_adherence_report,
    _audit,
)

# ─────────────────────────────────────────────────────────────────────────────
# MCP Server instance
# ─────────────────────────────────────────────────────────────────────────────
server = Server("curocadence-mcp")


@server.list_tools()
async def list_tools() -> list[mcp_types.Tool]:
    """List all available MCP tools."""
    return [
        mcp_types.Tool(
            name="get_medication_schedule",
            description="Get the current medication schedule for a user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "The unique identifier for the user.",
                    }
                },
                "required": ["user_id"],
            },
        ),
        mcp_types.Tool(
            name="add_medication",
            description="Add a medication to the user's schedule.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier."},
                    "drug_name": {"type": "string", "description": "Name of the medication."},
                    "dose": {"type": "string", "description": "Dosage (e.g. '500mg')."},
                    "times": {
                        "type": "string",
                        "description": "Dosing times as comma-separated string (e.g. '08:00,20:00').",
                    },
                },
                "required": ["user_id", "drug_name", "dose", "times"],
            },
        ),
        mcp_types.Tool(
            name="check_interaction",
            description="Check for known drug-drug interactions between two medications.",
            inputSchema={
                "type": "object",
                "properties": {
                    "drug_a": {"type": "string", "description": "First medication name."},
                    "drug_b": {"type": "string", "description": "Second medication name."},
                },
                "required": ["drug_a", "drug_b"],
            },
        ),
        mcp_types.Tool(
            name="log_dose_taken",
            description="Log that a medication dose was taken.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier."},
                    "drug_name": {"type": "string", "description": "Medication name."},
                    "timestamp": {
                        "type": "string",
                        "description": "ISO 8601 timestamp (e.g. '2024-01-15T08:00:00Z').",
                    },
                },
                "required": ["user_id", "drug_name", "timestamp"],
            },
        ),
        mcp_types.Tool(
            name="export_schedule_ics",
            description="Export the medication schedule as an iCalendar (.ics) file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier."}
                },
                "required": ["user_id"],
            },
        ),
        mcp_types.Tool(
            name="get_adherence_report",
            description="Generate an adherence report for a given date range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier."},
                    "date_range": {
                        "type": "string",
                        "description": "Date range string e.g. '2024-01-01 to 2024-01-07'.",
                    },
                },
                "required": ["user_id", "date_range"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[mcp_types.TextContent]:
    """Dispatch tool calls to the appropriate handler."""
    _audit("INFO", "mcp_tool_called", {"tool": name, "args_keys": list(arguments.keys())})

    try:
        if name == "get_medication_schedule":
            result = get_medication_schedule(arguments["user_id"])
        elif name == "add_medication":
            result = add_medication(
                arguments["user_id"],
                arguments["drug_name"],
                arguments["dose"],
                arguments["times"],
            )
        elif name == "check_interaction":
            result = check_interaction(arguments["drug_a"], arguments["drug_b"])
        elif name == "log_dose_taken":
            result = log_dose_taken(
                arguments["user_id"],
                arguments["drug_name"],
                arguments["timestamp"],
            )
        elif name == "export_schedule_ics":
            result = export_schedule_ics(arguments["user_id"])
        elif name == "get_adherence_report":
            result = get_adherence_report(arguments["user_id"], arguments["date_range"])
        else:
            result = json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        _audit("CRITICAL", "mcp_tool_error", {"tool": name, "error": str(e)})
        result = json.dumps({"error": str(e)})

    return [mcp_types.TextContent(type="text", text=result)]


async def main():
    """Run the MCP server using stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
