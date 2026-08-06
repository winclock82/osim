# OSIM — Operational Scheduling Intelligence Manager

## Problem Statement

Current situation is storing the batch job scheduling instructions and dependency 
data via Excel. This caused difficulties in locating & mapping dependencies and 
specific job scheduling information.

## Proposed Solution

Our solution will utilise RAG to feed the job scheduling information in Excel and 
using LLMs to analyse the scheduling information and dependencies, organise and 
cluster the jobs with similar runtimes through the use of embedding and vector 
stores. Our solution will then provide a chat interface for users to ask relevant 
information for specific job scheduling information and, if possible, display 
visualisations and charts to map out the sequencing of jobs.

## Impact

This will improve the visibility and makes visualisation of the job sequences easier, 
and allows job information to be organised and easily located through a chat interface. 
This solution is expected to be used whenever users are required to schedule and map 
out complex job sequences.

## Getting Started

### Prerequisites
Install the required dependencies:
\```
pip install -r requirements.txt
\```

### Running the App
\```
streamlit run main.py
\```

### Configuration
Create a `.streamlit/secrets.toml` file locally with your API keys. 
**Do not commit this file to the repository.**
