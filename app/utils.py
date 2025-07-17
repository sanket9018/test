"""
Utility functions
"""
import bcrypt

def hash_password(password: str) -> str:
    """Hash a password for storing."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a stored password against one provided by user."""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def success_response(data, message="Success", status_code=200):
    return {
        "data": data,
        "status_code": status_code,
        "message": message
    }

def error_response(message="An error occurred", status_code=400, data=None):
    return {
        "data": data,
        "status_code": status_code,
        "message": message
    }

