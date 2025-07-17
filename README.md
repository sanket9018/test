# Fitness App API

A FastAPI-based backend for a fitness application with PostgreSQL database integration.

## Prerequisites

- Python 3.8+
- PostgreSQL 13+
- pip (Python package manager)

## Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd fitness-app-api
   ```

2. **Create and activate a virtual environment**
   ```bash
   # On Windows
   python -m venv venv
   .\venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up PostgreSQL**
   - Make sure PostgreSQL is installed and running
   - Update the database connection details in `app/database.py` if needed

5. **Initialize the database**
   ```bash
   python init_db.py
   ```
   This will:
   - Create a new database called `fitness_db`
   - Create all necessary tables
   - Insert initial data

## Running the Application

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`

## API Endpoints

- `GET /` - Welcome message
- `GET /health` - Health check
- `GET /db-check` - Check database connection
- `GET /test-query` - Test database query

## API Documentation

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## Database Schema

The database schema includes tables for:
- Users and their profiles
- Exercises and equipment
- Workout plans and logs
- User goals and focus areas
- Health considerations

## Environment Variables

Create a `.env` file in the root directory with the following variables:

```
DB_USER=postgres
DB_PASSWORD=admin123
DB_HOST=localhost
DB_PORT=5432
DB_NAME=fitness_db
```
