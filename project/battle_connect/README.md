How to Run

python main.py {arguments}

Arguments:

"--username", Showdown username
"--password", Showdown password (optional, for guest login)
"--address", default="wss://sim3.psim.us/showdown/websocket", "WebSocket address"
"--team", Team in Showdown format (file path or team string)
"--mode", choices=["search", "accept", "challenge"], default="search", Battle mode: search (ranked), accept (challenges), challenge (specific user)
"--challenge-user", Username to challenge (required if mode is 'challenge')
