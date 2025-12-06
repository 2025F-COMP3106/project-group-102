import json
import sys

def flip_perspective(showdown_json):
    data = json.loads(showdown_json)

    # Flip players
    data['players'] = [data['players'][1], data['players'][0]]

    # Swap p1 and p2 keys in 'log' string
    log = data['log']
    log = log.replace('|p1|', '|TEMP|')
    log = log.replace('|p2|', '|p1|')
    log = log.replace('|TEMP|', '|p2|')

    log = log.replace('|p1a:', '|TEMPa:')
    log = log.replace('|p2a:', '|p1a:')
    log = log.replace('|TEMPa:', '|p2a:')

    data['log'] = log

    return json.dumps(data, indent=4)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python flip_perspective.py <input_file.json>")
        print("Output will be saved as <input_file>_flipped.json")
        sys.exit(1)

    input_file = sys.argv[1]

    try:
        with open(input_file, 'r') as f:
            original_json = f.read()

        flipped_json = flip_perspective(original_json)

        output_file = input_file
        with open(output_file, 'w') as f:
            f.write(flipped_json)

        print(f"Flipped JSON saved to: {output_file}")

    except FileNotFoundError:
        print(f"Error: File '{input_file}' not found.")
    except json.JSONDecodeError:
        print(f"Error: '{input_file}' is not valid JSON.")
    except Exception as e:
        print(f"Error: {str(e)}")