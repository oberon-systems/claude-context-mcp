# Usage

Practical daily operations using `make` targets.

## Essential Commands

| Target         | Description                                              |
| :------------- | :------------------------------------------------------- |
| `make up`      | Starts the entire stack (Postgres, MCP Server, Viewer).  |
| `make down`    | Stops the stack.                                         |
| `make index`   | Indexes the codebase defined in `.env`.                  |
| `make install` | Onboards a new codebase and indexes it.                  |
| `make status`  | Checks service health and connected MCP clients.         |
| `make clean`   | Wipes the database and containers. **Use with caution.** |

## MCP Tools

Once an agent connects to the MCP server, you can use the following tools:

- **`get_code_graph_neighbors`**: Inspect node relationships.
- **`search_code_nodes`**: Find nodes by name or ID.
- **`shortest_path`**: Find the path between two code entities.
- **`save_node_summary` / `get_node_summary`**: Add human-readable summaries to your code.
- **`save_plan` / `get_plans`**: Manage project roadmap plans.

## Summarization

The system can auto-summarize files using a local LLM:

```bash
make summarize PROJECT=$(pwd)
```

This requires downloading model weights (`make llm-model-install`) and can be time-intensive for large codebases.
