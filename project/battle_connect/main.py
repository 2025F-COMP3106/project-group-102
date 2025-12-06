import asyncio
import argparse
import json
import logging
import sys
import os
import atexit
import signal

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from battle_connect.websocket_client import PSWebsocketClient
from battle_connect.simple_battle import simple_battle, cleanup_all_replay_files
from battle_connect.config import GbolshnikConfig

logger = logging.getLogger(__name__)

# Register cleanup function to run on exit/crash
atexit.register(cleanup_all_replay_files)

# Also register signal handlers for graceful shutdown
def signal_handler(signum, frame):
    cleanup_all_replay_files()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


async def run_ranked_search(client: PSWebsocketClient, team: str = None):
    """Search for a ranked gen9 OU match."""
    logger.info("Searching for ranked gen9 OU match...")
    await client.search_for_match("gen9ou", team, [])
    winner = await simple_battle(client, "gen9ou")
    return winner


async def accept_challenge(client: PSWebsocketClient, team: str = None):
    """Accept incoming challenges for gen9 OU."""
    logger.info("Waiting for gen9 OU challenge...")
    await client.accept_challenge("gen9ou", team, None)
    winner = await simple_battle(client, "gen9ou")
    return winner


async def challenge_user_func(client: PSWebsocketClient, target_username: str, team: str = None):
    """Challenge a specific user to gen9 OU."""
    logger.info(f"Challenging {target_username} to gen9 OU...")
    await client.challenge_user(target_username, "gen9ou", team)
    winner = await simple_battle(client, "gen9ou")
    return winner


async def main(
    username: str,
    password: str = None,
    address: str = "wss://sim3.psim.us/showdown/websocket",
    team: str = None,
    mode: str = "search",
    challenge_user: str = None
):
    """Main entry point."""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Set config
    GbolshnikConfig.username = username
    
    # Handle team: if it's a file path, read it; otherwise use as-is
    if team:
        # Check if it's a file path
        team_paths = [
            team,
            os.path.join(os.path.dirname(__file__), team),
            os.path.join(os.path.dirname(__file__), "..", team)
        ]
        team_file = None
        for path in team_paths:
            if os.path.isfile(path):
                team_file = path
                break
        
        if team_file:
            logger.info(f"Reading team from file: {team_file}")
            with open(team_file, 'r', encoding='utf-8') as f:
                team = f.read().strip()
        else:
            logger.debug("Using team string directly")
    
    # Connect to Showdown
    try:
        client = await PSWebsocketClient.create(username, password, address)
        await client.login()
        logger.info(f"Successfully logged in as {username}")
    except Exception as e:
        logger.error(f"Failed to connect/login: {e}")
        return
    
    try:
        # Run battle based on mode
        if mode == "search":
            # Set team before searching (required for gen9ou)
            if team:
                await client.update_team("gen9ou", team)
                logger.info("Team updated")
            else:
                logger.warning("No team provided - gen9 OU requires a team. Search may fail.")
            await run_ranked_search(client, team)
        elif mode == "accept":
            await accept_challenge(client, team)
        elif mode == "challenge":
            if not challenge_user:
                logger.error("--challenge-user required when mode is 'challenge'")
                return
            await challenge_user_func(client, challenge_user, team)
        else:
            logger.error(f"Unknown mode: {mode}")
            return
            
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Error during battle: {e}", exc_info=True)
    finally:
        # Cleanup replay files on exit/crash
        from battle_connect.simple_battle import cleanup_all_replay_files
        cleanup_all_replay_files()
        
        await client.close()
        logger.info("Disconnected from Showdown")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pokemon Showdown Bot for gen9 OU")
    parser.add_argument("--username", required=True, help="Showdown username")
    parser.add_argument("--password", help="Showdown password (optional, for guest login)")
    parser.add_argument("--address", default="wss://sim3.psim.us/showdown/websocket",
                       help="WebSocket address")
    parser.add_argument("--team", help="Team in Showdown format (file path or team string)")
    parser.add_argument("--mode", choices=["search", "accept", "challenge"], default="search",
                       help="Battle mode: search (ranked), accept (challenges), challenge (specific user)")
    parser.add_argument("--challenge-user", help="Username to challenge (required if mode is 'challenge')")
    
    args = parser.parse_args()
    
    asyncio.run(main(
        username=args.username,
        password=args.password,
        address=args.address,
        team=args.team,
        mode=args.mode,
        challenge_user=args.challenge_user
    ))

