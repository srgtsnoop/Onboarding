# Refreshes demo data and launches the web server.
# Your templates and other work will be preserved.

# 1) Clean caches
Write-Host "Cleaning Python caches..."
Remove-Item -Recurse -Force .\Onboarding\__pycache\* -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force .\__pycache__ -ErrorAction SilentlyContinue

# 2) Install dependencies
Write-Host "Installing dependencies..."
pip install -r requirements.txt

# 3) Re-seed the database with demo data
Write-Host "Seeding database with demo data..."
python seed.py

# 4) Run the Flask development server
Write-Host "Starting Flask server..."
flask --app app run --debug
