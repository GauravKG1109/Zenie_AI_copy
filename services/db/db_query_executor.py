import decimal
from sqlalchemy import text
from core.database import engine


def _serialize_value(v):
    """Convert DB types that are not JSON-serializable to plain Python types."""
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def execute_query(sql_query: str):
    """
    Executes a raw SQL query against the database and returns the results.

    Args:
        sql_query (str): The SQL query to execute.
    """
    try:
        with engine.connect() as connection:
            result = connection.execute(text(sql_query))
            columns = list(result.keys())
            rows = result.fetchall()
            data = [
                {col: _serialize_value(val) for col, val in zip(columns, row)}
                for row in rows
            ]
            return {
                "success": True,
                "data": data,
                "row_count": len(data),
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }
