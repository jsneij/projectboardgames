#!/bin/bash
cd "$(dirname "$0")"

# Kill any existing process on port 8000
lsof -ti :8000 | xargs kill -9 2>/dev/null

# Start HTTP server in background
python3 -m http.server 8000 &
SERVER_PID=$!

# Wait for server to be ready
sleep 1

# Open dashboard in Chrome
open -a "Google Chrome" "http://localhost:8000/dashboard/dshb_bgg_collection.html"

echo ""
echo "  BGG Dashboard running at http://localhost:8000/dashboard/dshb_bgg_collection.html"
echo "  Server PID: $SERVER_PID"
echo ""
echo "  Press any key to stop the server..."
read -n 1

kill $SERVER_PID 2>/dev/null
echo "  Server stopped."
