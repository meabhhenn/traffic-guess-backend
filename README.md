Traffic Guess Game Backend

Backend for my Traffic Guess Game. It picks a real historical hour of Chicago Loop traffic and weather data, keeps the answer on the server, scores guesses, and serves the map.

Live backend: https://traffic-guess-backend.onrender.com
Frontend: https://meabhhenn.github.io/traffic.html

Endpoints

GET /api/round
Returns a round: round_id, date, time, day, weather, and the overall speed range. Does not include the answer.

POST /api/guess
Accepts JSON {"round_id": 123, "guess": 18.5}. Returns the actual speed, the difference, and points (3 if within 1 mph, 1 if within 3 mph, otherwise 0). Returns a 400 error with a message for empty, non-numeric, or out of range guesses, or an invalid round_id.

GET /api/map
Returns a map image of the region, fetched from Mapbox by the backend.

How the frontend uses it

On load, traffic.html calls /api/round and shows the clues, and loads the map from /api/map. When the player submits, it sends the guess to /api/guess and shows the result. If a request fails, it shows the error message and a retry button.

Running locally

pip install -r requirements.txt
Create a .env file with MAPBOX_TOKEN=your_token
python app.py
The server runs at http://127.0.0.1:5050

Secrets

The Mapbox token is stored as an environment variable on Render and in a local .env file, which is in .gitignore. It is never in the repo or the frontend.
