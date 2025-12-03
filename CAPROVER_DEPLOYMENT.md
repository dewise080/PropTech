# CapRover Deployment Guide for Istanbul PropTech

## Prerequisites

1. CapRover server set up and running
2. CapRover CLI installed: `npm install -g caprover`
3. Your database SQL file: `full_migration_data.sql`

## Deployment Steps

### 1. Create PostgreSQL App in CapRover

First, create a PostgreSQL database with PostGIS extension:

1. Log in to your CapRover dashboard
2. Go to **Apps** → **One-Click Apps/Databases**
3. Search for **PostgreSQL** and deploy it
4. Name it: `istanbul-proptech-db`
5. Set password (remember this!)

### 2. Enable PostGIS Extension

After the database is created, connect to it and enable PostGIS:

```bash
# Get into the database container
docker exec -it $(docker ps -q -f name=srv-captain--istanbul-proptech-db) psql -U postgres
```

Then run:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
\q
```

### 3. Import Your Data

Copy your SQL file to the database container and import:

```bash
# Copy the SQL file
docker cp full_migration_data.sql $(docker ps -q -f name=srv-captain--istanbul-proptech-db):/tmp/

# Import the data
docker exec -it $(docker ps -q -f name=srv-captain--istanbul-proptech-db) psql -U postgres -d postgres -f /tmp/full_migration_data.sql
```

### 4. Create the Django App

1. In CapRover dashboard, go to **Apps**
2. Click **Create App**
3. Name it: `istanbul-proptech`
4. Enable **Has Persistent Data** (for media files)

### 5. Configure Environment Variables

In the app settings, add these environment variables:

```
DJANGO_SECRET_KEY=your-secret-key-here-change-this
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=istanbul-proptech.your-domain.com,*.caprover.com
POSTGRES_DB=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your-db-password-from-step-1
POSTGRES_HOST=srv-captain--istanbul-proptech-db
POSTGRES_PORT=5432
```

### 6. Deploy the App

From your project directory:

```bash
# Initialize CapRover
caprover login

# Deploy the app
caprover deploy
```

When prompted:
- Select your server
- Enter app name: `istanbul-proptech`
- It will use the `captain-definition` file automatically

### 7. Enable HTTPS

1. Go to your app in CapRover dashboard
2. Enable **HTTPS**
3. Enable **Force HTTPS**
4. Enable **Websocket Support** (optional)

### 8. Configure Persistent Storage (Optional)

If you want to persist uploaded images:

1. In app settings, go to **App Configs**
2. Add persistent directory: `/app/listings` → `/app/listings`
3. Save and restart

## Alternative: Using Docker Compose Locally

For local testing with Docker:

```bash
# Start everything
docker-compose up -d

# Check logs
docker-compose logs -f web

# Stop everything
docker-compose down
```

## Database Backup

To backup your database:

```bash
docker exec $(docker ps -q -f name=srv-captain--istanbul-proptech-db) pg_dump -U postgres postgres > backup.sql
```

## Troubleshooting

### Database Connection Issues

Check if the database is reachable:

```bash
# From within the web container
docker exec -it srv-captain--istanbul-proptech ping srv-captain--istanbul-proptech-db
```

### GDAL/GEOS Library Issues

If you see GDAL errors, the Dockerfile includes all necessary PostGIS dependencies.

### Static Files Not Loading

Run collectstatic manually:

```bash
docker exec -it srv-captain--istanbul-proptech python manage.py collectstatic --noinput
```

### View Logs

```bash
# CapRover app logs
docker logs srv-captain--istanbul-proptech -f

# Database logs
docker logs srv-captain--istanbul-proptech-db -f
```

## Production Checklist

- [ ] Change `DJANGO_SECRET_KEY` to a secure random value
- [ ] Set `DJANGO_DEBUG=0`
- [ ] Configure proper `DJANGO_ALLOWED_HOSTS`
- [ ] Enable HTTPS
- [ ] Set strong database password
- [ ] Configure regular database backups
- [ ] Set up monitoring and error tracking
- [ ] Configure email settings for Django
- [ ] Set up log aggregation

## Scaling

To scale your app in CapRover:

1. Go to app settings
2. Under **App Configs** → **Instance Count**
3. Increase the number of instances
4. The load balancer will automatically distribute traffic

## Database Restore

To restore from backup:

```bash
docker cp backup.sql $(docker ps -q -f name=srv-captain--istanbul-proptech-db):/tmp/
docker exec -it $(docker ps -q -f name=srv-captain--istanbul-proptech-db) psql -U postgres -d postgres -f /tmp/backup.sql
```
