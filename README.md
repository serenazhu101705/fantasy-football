Claude is now the final contextual decision-maker for lineup/ROS questions, weekly_score, ros_score, optimizer, and deterministic comparisons are advisory, hard eligibility and provider facts remain authoritative, FantasyCalc is a free ROS/market-value fallback (not ECR/projection), Sleeper is a free injury/practice/depth-chart fallback (not ECR/projection), Fallback responses are cached locally to avoid excessive requests.

## Running the app locally
1. `source .venv/bin/activate` to activate the virtual environment
2. `cd` into the project directory
3. run: `uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000`
4. open a new terminal and run: 
    - `cd web`
    - `npm install`
    - `npm run dev`
5. follow the link to [http://127.0.0.1:5173/](http://127.0.0.1:5173/)