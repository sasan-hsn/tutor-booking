from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase

from accounts.models import User
from wallet.tests.concurrency_utils import run_concurrently


class PostgresInfrastructureTests(TestCase):
    def test_engine_is_postgresql(self):
        """Asserts that the active database connection under test is PostgreSQL."""
        self.assertEqual(connection.vendor, "postgresql")

    def test_select_for_update_supported_inside_atomic(self):
        """Validates that select_for_update() can be acquired inside an atomic block without throwing unsupported backend errors."""
        user = User.objects.create_user(
            username="lockuser",
            password="testpassword123",
            role=User.Role.STUDENT,
        )

        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=user.pk)
            self.assertEqual(locked_user.pk, user.pk)

    def test_database_trigger_execution_supported(self):
        """Validates that PostgreSQL triggers (BEFORE UPDATE) can be created and execute."""
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TEMPORARY TABLE test_trigger_table (
                    id serial PRIMARY KEY,
                    val text
                );
                CREATE OR REPLACE FUNCTION test_prevent_update()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'Updates are prohibited by trigger';
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER trg_test_prevent_update
                BEFORE UPDATE ON test_trigger_table
                FOR EACH ROW EXECUTE FUNCTION test_prevent_update();

                INSERT INTO test_trigger_table (val) VALUES ('immutable');
                """)
            with self.assertRaises(Exception) as ctx:
                cursor.execute(
                    "UPDATE test_trigger_table SET val = 'mutated' WHERE val = 'immutable';"
                )
            self.assertIn("Updates are prohibited by trigger", str(ctx.exception))


class PostgresConcurrencyInfrastructureTests(TransactionTestCase):
    def test_parallel_thread_connection_without_deadlock(self):
        """Verifies 5 background threads can open independent database connections and commit transactions without deadlock."""
        num_threads = 5

        def _task(i):
            with transaction.atomic():
                user = User.objects.create_user(
                    username=f"thread_user_{i}",
                    password="password123",
                    role=User.Role.STUDENT,
                )
                return user.id

        tasks = [lambda i=i: _task(i) for i in range(num_threads)]
        results = run_concurrently(tasks, timeout=10.0)

        # Assert all 5 threads completed successfully without exceptions
        for res, exc in results:
            self.assertIsNone(exc)
            self.assertIsNotNone(res)

        created_count = User.objects.filter(username__startswith="thread_user_").count()
        self.assertEqual(created_count, num_threads)
