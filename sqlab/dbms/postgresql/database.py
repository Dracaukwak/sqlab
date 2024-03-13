import re
import psycopg2

from ...database import AbstractDatabase
from ...text_tools import FAIL, OK, RESET, WARNING
from ...text_tools import repr_single

class Database(AbstractDatabase):

    def connect(self):
        try:
            self.cnx = psycopg2.connect(**self.config["cnx"])
            # Disable transactions and autocommit all statements
            self.cnx.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            with self.cnx.cursor() as cursor:
                # Print a message with the PostgreSQL server version and the database name
                cursor.execute("SELECT version(), current_database()")
                db_info = cursor.fetchone()
                self.dbms_version = db_info[0].split()[1]
                db_name = db_info[1]
                print(f"{OK}Connected to PostgreSQL {self.dbms_version} with database {repr(db_name)}.{RESET}")
        except (psycopg2.DatabaseError, Exception) as error:
            print(error)

    def get_headers(self, table, keep_auto_increment_columns=True):
        query = f"""
            SELECT column_name, column_default
            FROM information_schema.columns
            WHERE table_name = '{table}'
                AND column_name != 'hash'
                AND (column_default IS NULL OR NOT column_default LIKE 'nextval(%') -- Exclude auto_increment columns
            ORDER BY ordinal_position
        """
        if keep_auto_increment_columns:
            query = re.sub(r"(?m)^.* -- Exclude auto_increment columns\n", "", query)
        headers = []
        with self.cnx.cursor() as cursor:
            cursor.execute(query)
            headers = [row[0] for row in cursor.fetchall()]
        return headers

    def encrypt(self, clear_text, token):
        """
        In PostgreSQL, the function pgp_sym_encrypt() takes a textual key, not a numeric one.
        Since the user passes a numeric token to our SQL function decrypt, we need to normalize
        this number by stripping the leading zeros before casting it to string.
        """
        clear_text = f"E{repr_single(clear_text)}" # E prefix for escaping single quote with a \'
        token = repr(token.lstrip("0"))
        query = f"SELECT encode(pgp_sym_encrypt({clear_text}, {token}, 'cipher-algo=aes'), 'hex')"
        with self.cnx.cursor() as cursor:
            cursor.execute(query)
            encrypted_hex = cursor.fetchone()[0]
            return fr"'\x{encrypted_hex}'"
    
    def decrypt(self, encrypted, token):
        token = token.lstrip("0")
        query = fr"SELECT pgp_sym_decrypt({encrypted}, {repr(token)}, 'cipher-algo=aes')"
        return self.execute_select(query)[2][0][0]

    def execute_non_select(self, query):
        statements = [
            s
            for statement in re.split(r";\s*\n+", query)  # Split on trailing semicolons
            if (s := statement.strip()) # and remove empty strings
        ]
        rowcounts = []
        with self.cnx.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
                rowcounts.append(cursor.rowcount)
        return sum(rowcounts)
    
    def parse_ddl(self, queries):
        triple = re.split(r"(?mi)^(?:\\c .+|-- FK\b.*)", queries, 2)
        self.create_db_queries = triple[0]
        self.create_tables_queries = triple[1]
        try:
            self.add_fk_constraints_queries = triple[2]
        except IndexError:
            print(f"{FAIL}The foreign key constraints definitions must be separated from the previous parts with a -- FK comment.{RESET}")
        self.drop_fk_constraints_queries = re.sub(
            r"(?s)\bADD CONSTRAINT\s+(.+?)\s+FOREIGN KEY\b.+?([,;]\n)",
            r"DROP CONSTRAINT \1\2",
            self.add_fk_constraints_queries,
        )
    
    @staticmethod
    def reset_table_statement(table: str) -> str:
        return f"TRUNCATE TABLE {table} RESTART IDENTITY;\n"
