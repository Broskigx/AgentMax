"""MCP (Model Context Protocol) layer for AgentMax.

Lets AgentMax act as an MCP **client** (connect to external MCP servers,
discover and call their tools) and, via the generic :class:`MCPServer`, expose
its own callables as MCP tools. :class:`MCPManager` bridges discovered tools
into AgentMax's tool registry.

Portions adapted from OpenJarvis (Apache-2.0). See core/mcp/NOTICE.
"""

from core.mcp.client import MCPClient, MCPToolSpec
from core.mcp.manager import MCPManager, extract_text
from core.mcp.protocol import (
    MCPError,
    MCPNotification,
    MCPRequest,
    MCPResponse,
)
from core.mcp.registry_server import build_tool_mcp_server, tool_to_mcp
from core.mcp.server import MCPServer, MCPTool
from core.mcp.transport import (
    InProcessTransport,
    MCPTransport,
    SSETransport,
    StdioTransport,
    StreamableHTTPTransport,
)

__all__ = [
    "MCPClient",
    "MCPToolSpec",
    "MCPManager",
    "extract_text",
    "MCPError",
    "MCPNotification",
    "MCPRequest",
    "MCPResponse",
    "MCPServer",
    "MCPTool",
    "build_tool_mcp_server",
    "tool_to_mcp",
    "InProcessTransport",
    "MCPTransport",
    "SSETransport",
    "StdioTransport",
    "StreamableHTTPTransport",
]
