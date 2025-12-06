def normalize_name(name):
    return (
        name.replace(" ", "")
        .replace("-", "")
        .replace(".", "")
        .replace("'", "")
        .replace("%", "")
        .replace("*", "")
        .replace(":", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
        .lower()
        .encode("ascii", "ignore")
        .decode("utf-8")
    )


def json_to_packed(json_team):
    """Convert a list of Pokémon dicts to packed format."""
    def from_json(j):
        # Get EVs in order: hp, atk, def, spa, spd, spe
        evs = j.get("evs", {})
        ev_string = ",".join([
            str(evs.get("hp", "")),
            str(evs.get("atk", "")),
            str(evs.get("def", "")),
            str(evs.get("spa", "")),
            str(evs.get("spd", "")),
            str(evs.get("spe", ""))
        ])
        
        # Get IVs in order: hp, atk, def, spa, spd, spe
        ivs = j.get("ivs", {})
        iv_string = ",".join([
            str(ivs.get("hp", "")),
            str(ivs.get("atk", "")),
            str(ivs.get("def", "")),
            str(ivs.get("spa", "")),
            str(ivs.get("spd", "")),
            str(ivs.get("spe", ""))
        ])
        
        return "{}|{}|{}|{}|{}|{}|{}|{}|{}|{}|{}|{},{},{},{},{},{}".format(
            j.get("name", ""),
            j.get("species", ""),
            j.get("item", ""),
            j.get("ability", ""),
            ",".join(j.get("moves", [])),
            j.get("nature", ""),
            ev_string,
            j.get("gender", ""),
            iv_string,
            j.get("shiny", ""),
            j.get("level", ""),
            j.get("happiness", ""),
            j.get("pokeball", ""),
            j.get("hiddenpowertype", ""),
            j.get("gigantamax", ""),
            j.get("dynamaxlevel", ""),
            j.get("tera_type", ""),
        )
    packed_team_string = "]".join((from_json(p) for p in json_team))
    return packed_team_string


def single_pokemon_export_to_dict(pkmn_export_string):
    """Convert a single Pokémon from pokepaste format to dict."""
    def get_species_in_parentheses(s):
        if "(" in s and ")" in s:
            species = s[s.find("(") + 1 : s.find(")")]
            nickname = s.replace(species, "").replace("()", "")
            return species.strip(), nickname.strip()
        return None, None

    pkmn_dict = {
        "name": "",
        "species": "",
        "level": "",
        "tera_type": "",
        "gender": "",
        "item": "",
        "ability": "",
        "moves": [],
        "shiny": "",
        "nature": "",
        "ivs": {
            "hp": "",
            "atk": "",
            "def": "",
            "spa": "",
            "spd": "",
            "spe": "",
        },
        "evs": {
            "hp": "",
            "atk": "",
            "def": "",
            "spa": "",
            "spd": "",
            "spe": "",
        },
    }

    pkmn_info = pkmn_export_string.split("\n")
    name = pkmn_info[0].split("@")[0]

    if "(M)" in name:
        pkmn_dict["gender"] = "M"
        name = name.replace("(M)", "")
    if "(F)" in name:
        pkmn_dict["gender"] = "F"
        name = name.replace("(F)", "")

    species, nickname = get_species_in_parentheses(name)
    if species:
        pkmn_dict["species"] = normalize_name(species.strip())
        pkmn_dict["name"] = nickname.strip()
    else:
        pkmn_dict["species"] = normalize_name(name.strip())

    if "@" in pkmn_info[0]:
        pkmn_dict["item"] = normalize_name(pkmn_info[0].split("@")[1].strip())

    for line in map(str.strip, pkmn_info[1:]):
        if line.startswith("Ability: "):
            pkmn_dict["ability"] = normalize_name(line.split("Ability: ")[-1])
        if line == "Shiny: Yes":
            pkmn_dict["shiny"] = "S"
        elif line.startswith("Tera Type: "):
            pkmn_dict["tera_type"] = normalize_name(line.split("Tera Type: ")[-1])
        elif line.startswith("Level: "):
            pkmn_dict["level"] = normalize_name(line.split("Level: ")[-1])
        elif line.startswith("EVs: "):
            evs = line.split("EVs: ")[-1]
            for ev in evs.split("/"):
                ev = ev.strip()
                if ev:
                    parts = ev.split()
                    if len(parts) >= 2:
                        amount = parts[0]
                        stat = normalize_name(parts[1])
                        pkmn_dict["evs"][stat] = amount
        elif line.startswith("IVs: "):
            ivs = line.split("IVs: ")[-1]
            for iv in ivs.split("/"):
                iv = iv.strip()
                if iv:
                    parts = iv.split()
                    if len(parts) >= 2:
                        amount = parts[0]
                        stat = normalize_name(parts[1])
                        pkmn_dict["ivs"][stat] = amount
        elif line.endswith("Nature"):
            pkmn_dict["nature"] = normalize_name(line.split("Nature")[0].strip())
        elif line.startswith("-"):
            pkmn_dict["moves"].append(normalize_name(line[1:].strip()))

    return pkmn_dict


def export_to_packed(export_string):
    """Convert pokepaste format string to packed format string."""
    team_dict = list()
    team_members = export_string.split("\n\n")

    for pkmn in filter(None, team_members):
        pkmn_dict = single_pokemon_export_to_dict(pkmn)
        team_dict.append(pkmn_dict)

    return json_to_packed(team_dict)
