#!/usr/bin/env bash
# setup.sh — one-click installer for PropTech
set -e

cd "$(dirname "$0")"

echo "==> Installing system packages (GDAL, PostGIS, PostgreSQL)..."
sudo apt install -y \
    gdal-bin libgdal-dev libgeos-dev libproj-dev \
    postgresql postgresql-contrib postgis

echo "==> Starting PostgreSQL..."
sudo service postgresql start

# Read DB config from env or fall back to settings defaults
DB_NAME="${POSTGRES_DB:-istanbul_proptech}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASS="${POSTGRES_PASSWORD:-12345}"

echo "==> Setting postgres user password..."
sudo -u postgres psql -c "ALTER USER ${DB_USER} PASSWORD '${DB_PASS}';"

echo "==> Creating database '${DB_NAME}' if it doesn't exist..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" \
    | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"

echo "==> Enabling PostGIS extension on '${DB_NAME}'..."
sudo -u postgres psql -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS postgis;"
sudo -u postgres psql -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS postgis_topology;"

echo "==> Finding GDAL library path and patching settings.py..."
GDAL_LIB=$(find /usr/lib -name "libgdal.so.*" ! -name "*.so.*.*" 2>/dev/null | head -1)
if [ -z "$GDAL_LIB" ]; then
    echo "ERROR: Could not find libgdal.so — is libgdal-dev installed?"
    exit 1
fi
echo "    Found: $GDAL_LIB"

SETTINGS="IstanbulPropTech/settings.py"
if grep -q "GDAL_LIBRARY_PATH" "$SETTINGS"; then
    # Update existing line
    sed -i "s|GDAL_LIBRARY_PATH = .*|GDAL_LIBRARY_PATH = '${GDAL_LIB}'|" "$SETTINGS"
else
    # Insert after BASE_DIR line
    sed -i "/^BASE_DIR = /a GDAL_LIBRARY_PATH = '${GDAL_LIB}'" "$SETTINGS"
fi
echo "    GDAL_LIBRARY_PATH set to: $GDAL_LIB"

echo "==> Setting up Python virtual environment..."
if [ ! -f "venv/bin/python3" ] || [ ! -x "venv/bin/python3" ]; then
    rm -rf venv
    python3 -m venv venv
fi
source venv/bin/activate

echo "==> Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "==> Running Django migrations..."
python3 manage.py migrate

echo ""
echo "Done! Activate venv and start the server:"
echo "  source venv/bin/activate && python3 manage.py runserver"
