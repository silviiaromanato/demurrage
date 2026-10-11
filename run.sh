set -euo pipefail

cd "$(dirname "$0")"

uv sync
uv run demurrage batch
DEMURRAGE_RESOLVER_MODEL=openai/gpt-6-sol uv run demurrage report KB-2603 

uv run demurrage check

