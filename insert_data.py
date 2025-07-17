import psycopg2
import os
import re
import pandas as pd  # <-- Import pandas
from dotenv import load_dotenv

# --- Configuration ---
# Load environment variables from a .env file if it exists
load_dotenv()

# IMPORTANT: Update this path to point to your actual Excel file
EXCEL_FILE_PATH = "exercises.xlsx"  # <--- CHANGE THIS LINE

# Database connection parameters (reads from .env file or uses defaults)
DB_NAME = os.getenv("DB_NAME", "fitness_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "admin123")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")


def read_data_from_excel(file_path):
    """
    Reads exercise data from an Excel file and returns it as a list of dictionaries.
    """
    try:
        # Use pandas to read the first sheet of the Excel file
        df = pd.read_excel(file_path)
        # Convert the DataFrame to a list of dictionaries for easy processing
        exercises = df.to_dict(orient='records')
        print(f"Successfully read {len(exercises)} rows from '{file_path}'.")
        return exercises
    except FileNotFoundError:
        print(f"FATAL ERROR: The file '{file_path}' was not found. Please check the EXCEL_FILE_PATH variable.")
        return []
    except Exception as e:
        print(f"FATAL ERROR: An error occurred while reading the Excel file: {e}")
        return []

def clean_exercise_name(name):
    """Removes parenthetical remarks from the end of an exercise name."""
    # Ensure the input is a string before processing
    name_str = str(name)
    return re.sub(r'\s*\([^)]*\)$', '', name_str).strip()

def get_id_from_table(cursor, table_name, value):
    """
    Fetches the ID for a given value from a lookup table.
    Uses case-insensitive matching for robustness.
    """
    # Using ILIKE handles cases like 'Body weight' vs 'body weight'.
    query = f"SELECT id FROM {table_name} WHERE name ILIKE %s;"
    cursor.execute(query, (str(value),)) # Ensure value is a string
    result = cursor.fetchone()
    return result[0] if result else None

def populate_exercises():
    """
    Connects to the database and populates the exercises and related tables
    from the data loaded from an Excel file.
    """
    # 1. Load data from the Excel file first
    exercise_data = read_data_from_excel(EXCEL_FILE_PATH)
    if not exercise_data:
        print("No data was loaded from the Excel file. Halting execution.")
        return

    conn = None
    inserted_count = 0
    skipped_count = 0
    failed_count = 0
    
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
        )
        cursor = conn.cursor()
        print("\nSuccessfully connected to the database. Starting data population...")

        # Caches to avoid repeated DB lookups for the same ID
        focus_area_cache = {}
        equipment_cache = {}

        for row_num, row in enumerate(exercise_data, 1):
            try:
                # IMPORTANT: Ensure these keys match your Excel column headers exactly
                original_name = row['Tur']
                cleaned_name = clean_exercise_name(original_name)
                exercise_type = str(row['Exercise type']).lower()
                body_part = row['BodyPart']
                equipment_name = row['Equipment']

                # --- 2. Look up Foreign Keys with Caching ---
                if body_part not in focus_area_cache:
                    focus_area_cache[body_part] = get_id_from_table(cursor, 'focus_areas', body_part)
                focus_area_id = focus_area_cache[body_part]

                if not focus_area_id:
                    print(f"WARNING (Row {row_num}): Skipping '{original_name}'. Reason: Focus Area '{body_part}' not found in the database.")
                    failed_count += 1
                    continue
                
                if equipment_name not in equipment_cache:
                    equipment_cache[equipment_name] = get_id_from_table(cursor, 'equipment', equipment_name)
                equipment_id = equipment_cache[equipment_name]

                if not equipment_id:
                    print(f"WARNING (Row {row_num}): Skipping '{original_name}'. Reason: Equipment '{equipment_name}' not found in the database.")
                    failed_count += 1
                    continue

                # --- 3. Insert into 'exercises' table (if it doesn't exist) ---
                insert_exercise_sql = """
                    INSERT INTO exercises (name, description, video_url, exercise_type, primary_focus_area_id, difficulty)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO NOTHING
                    RETURNING id;
                """
                cursor.execute(insert_exercise_sql, (
                    cleaned_name, "", "", exercise_type, focus_area_id, 'intermediate'
                ))
                
                result = cursor.fetchone()
                exercise_id = None
                
                if result is None:
                    skipped_count += 1
                    cursor.execute("SELECT id FROM exercises WHERE name = %s", (cleaned_name,))
                    existing_exercise = cursor.fetchone()
                    if existing_exercise:
                        exercise_id = existing_exercise[0]
                else:
                    exercise_id = result[0]
                    inserted_count += 1
                
                if not exercise_id:
                    print(f"ERROR (Row {row_num}): Could not retrieve ID for '{cleaned_name}'. Skipping junction tables.")
                    failed_count += 1
                    continue

                # --- 4. Insert into Junction Tables (if links don't exist) ---
                insert_focus_sql = """
                    INSERT INTO exercise_focus_areas (exercise_id, focus_area_id, is_primary)
                    VALUES (%s, %s, %s) ON CONFLICT (exercise_id, focus_area_id) DO NOTHING;
                """
                cursor.execute(insert_focus_sql, (exercise_id, focus_area_id, True))

                insert_equip_sql = """
                    INSERT INTO exercise_equipment (exercise_id, equipment_id)
                    VALUES (%s, %s) ON CONFLICT (exercise_id, equipment_id) DO NOTHING;
                """
                cursor.execute(insert_equip_sql, (exercise_id, equipment_id))

                conn.commit()

            except KeyError as e:
                print(f"FATAL ERROR (Row {row_num}): Missing column in Excel file: {e}. Please ensure your file has 'Tur', 'Exercise type', 'BodyPart', and 'Equipment' columns.")
                return 
            except psycopg2.Error as e:
                print(f"DATABASE ERROR (Row {row_num}) for '{row.get('Tur', 'N/A')}': {e}")
                if conn: conn.rollback()
                failed_count += 1
            except Exception as e:
                print(f"UNEXPECTED ERROR (Row {row_num}) for '{row.get('Tur', 'N/A')}': {e}")
                if conn: conn.rollback()
                failed_count += 1

        print("\n--- Data Population Summary ---")
        print(f"New exercises inserted: {inserted_count}")
        print(f"Exercises already existed (skipped): {skipped_count}")
        print(f"Rows failed (e.g., missing lookups): {failed_count}")
        print("---------------------------------")

    except psycopg2.OperationalError as e:
        print(f"DATABASE CONNECTION ERROR: Could not connect to the database '{DB_NAME}'.")
        print(f"Please check your connection details and ensure the database exists and is running.")
        print(f"Details: {e}")
    except Exception as e:
        print(f"An unexpected script error occurred: {e}")
    finally:
        if conn is not None:
            conn.close()
            print("Database connection closed.")

if __name__ == "__main__":
    populate_exercises()