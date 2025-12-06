# Pokémon Showdown Battle Agent (COMP3106 Group 102)

This repository implements an end-to-end pipeline and battle agent for competitive Pokémon Showdown (Gen 9) using Python and Node.js, covering data collection from replays, dataset creation, model training, and online battle automation.[web:1][web:2]

## Overview

The project focuses on turning Pokémon Showdown replays into structured, machine-learning-ready datasets and using trained models to control an automated battle bot on the live server.[web:1][web:2] Core components include replay scraping and parsing, feature engineering and vectorization, an inference-time agent, and a branching engine to evaluate possible moves.[web:2]

## Features

- Scrape or ingest Pokémon Showdown replay logs and store them in a consistent directory layout.[web:1][web:2]  
- Parse raw logs into structured JSON with per-turn, per-player game state information.[web:2]  
- Build Pokémon and move datasets (including Gen 9 learnsets) for use in downstream processing.[web:2]  
- Construct vocabularies and vectorized representations of game states for model training.[web:2]  
- Run an inference agent that scores legal actions for a given state.[web:2]  
- Branch and evaluate hypothetical next states using a Node.js branching tool.[web:2]  
- Connect to Pokémon Showdown and play battles automatically through a battle connector.[web:2]

## Project Structure

- `project/` – Main source code:  
  - Replay scraping and parsing scripts  
  - Dataset and vocabulary builders  
  - State vectorization utilities  
  - Inference agent and branching logic  
  - Battle connector for online play[web:2]  

- `data/` – Data files and artifacts:  
  - Pokémon and move metadata for Gen 9  
  - Learnset mappings  
  - Raw replay logs and parsed JSON  
  - Example state and action files used by the agent[web:2]  

Additional configuration files at the root define Python and Node.js dependencies needed to run the full pipeline.[web:1][web:2]

## Setup

1. **Clone the repository**

`git clone https://github.com/2025F-COMP3106/project-group-102.git`
`cd project-group-102`

2. **Install Python dependencies**

`pip install -r requirements.txt`

3. **Install Node.js dependencies**

`npm install`


These steps prepare the environment for running the data pipeline, agent, and branching tools.[web:1][web:2]

## Data Pipeline

1. **Collect replays**

- Place raw Pokémon Showdown replay logs into the appropriate folder under `data/` (for example, `data/replays/`).[web:2]  

2. **Parse replays**

- Run the replay parsing scripts in `project/` to convert raw logs into structured JSON replays with turns and events.[web:2]  

3. **Build datasets**

- Use the Pokémon and move dataset builders to generate Gen 9 metadata and learnset information.[web:2]  
- Build vocabularies and create vectorized state representations suitable for model training.[web:2]  

4. **Train models**

- Open the supplied modeling notebook and train a model on the vectorized data, saving weights in a format compatible with the inference agent.[web:2]  

## Agent and Battle Bot

- Configure the inference script to load the trained model and read vectorized state input describing the current battle.[web:2]  
- Use the branching tool to enumerate possible next states and score them with the agent to select actions.[web:2]  
- Configure login details, format, and team files in the battle connector, then start the bot to queue and play matches on Pokémon Showdown automatically.[web:2]

## Course and Contributors

This project was developed for the COMP3106 course by Group 102 in the 2025F offering.[web:1] The repository is maintained by the listed student contributors in the course GitHub organization, who extend and improve the pipeline, models, and agent behavior over time.[web:1][web:2]
