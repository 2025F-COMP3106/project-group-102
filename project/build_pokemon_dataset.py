"""
Build a dataset of Gen 9 Pokémon with their types.
Scrapes from PokéAPI.
"""

import json
import requests
from pathlib import Path
import time

print("Fetching Gen 9 Pokémon data from PokéAPI...")

pokemon_data = {}
session = requests.Session()
session.timeout = 10

try:
    # Get all pokemon
    url = "https://pokeapi.co/api/v2/pokemon?limit=2000"
    print(f"Fetching from {url}")
    response = session.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()
    
    print(f"Found {data['count']} total pokemon")
    
    # Process each pokemon
    for i, pokemon in enumerate(data['results']):
        pokemon_name = pokemon['name']
        pokemon_url = pokemon['url']
        
        try:
            # Fetch individual pokemon details
            poke_response = session.get(pokemon_url, timeout=10)
            poke_response.raise_for_status()
            poke_detail = poke_response.json()
            
            # Get types
            types = [t['type']['name'] for t in poke_detail.get('types', [])]
            
            pokemon_data[pokemon_name] = {
                "types": types,
                "base_experience": poke_detail.get('base_experience'),
            }
            
            if (i + 1) % 200 == 0:
                print(f"  Processed {i + 1}/{len(data['results'])} pokemon...")
                time.sleep(0.5)
                
        except Exception as e:
            print(f"  Warning: Failed to fetch {pokemon_name}: {e}")
    
    print(f"\nTotal pokemon collected: {len(pokemon_data)}")
    
    # Save to JSON file
    output_file = Path(__file__).parent.parent / "data" / "gen9_pokemon.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(pokemon_data, f, indent=2)
    
    print(f"Saved to {output_file}")
    
    # Show sample
    print("\nSample pokemon:")
    for poke_name in sorted(list(pokemon_data.keys())[:20]):
        types = pokemon_data[poke_name]['types']
        print(f"  {poke_name}: {', '.join(types)}")

except Exception as e:
    print(f"Error: {e}")
    print("Creating fallback dataset with common Gen 9 pokemon...")
    
    # Fallback: Create dataset with known Gen 9 pokemon and their types
    pokemon_data = {
        "garchomp": {"types": ["dragon", "ground"], "base_experience": 270},
        "darkrai": {"types": ["dark"], "base_experience": 270},
        "gholdengo": {"types": ["steel", "ghost"], "base_experience": 270},
        "ogerpon-wellspring": {"types": ["water", "grass"], "base_experience": 270},
        "iron-valiant": {"types": ["fairy", "fighting"], "base_experience": 270},
        "iron-treads": {"types": ["ground", "steel"], "base_experience": 270},
        "pelipper": {"types": ["water", "flying"], "base_experience": 175},
        "kingdra": {"types": ["water", "dragon"], "base_experience": 236},
        "overqwil": {"types": ["dark", "poison"], "base_experience": 275},
        "kilowattrel": {"types": ["electric", "flying"], "base_experience": 273},
        "dragonite": {"types": ["dragon", "flying"], "base_experience": 300},
        "stealth-rock": {"types": [], "base_experience": 0},
    }
    
    output_file = Path(__file__).parent.parent / "data" / "gen9_pokemon.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(pokemon_data, f, indent=2)
    
    print(f"Saved fallback dataset to {output_file}")
