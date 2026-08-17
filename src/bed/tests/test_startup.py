#!/usr/bin/env python3
# bed/tests/test_startup.py
# Tests for bed startup: bbsengine6 startup + bed role creation

import asyncio
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/home/opencode/data/work/bed/src")


class TestBedStartupRoleCreation(unittest.TestCase):
    """Test the bed role creation logic in bed.startup."""

    def _make_args(self):
        return MagicMock(
            databasename="zoid6",
            databasehost="localhost",
            databaseport=5432,
            databaseuser="postgres",
            databasepassword=None,
            debug=False,
        )

    @patch("bed.startup.database")
    def test_ensure_bed_role_creates_when_missing(self, mock_db):
        """_ensure_bed_role creates the bed role when it does not exist."""
        from bed.startup import _ensure_bed_role, BED_ROLE

        mock_conn = MagicMock()
        mock_db.rolexists.return_value = False
        mock_db.createrol.return_value = True
        mock_db.manage_schema_priv.return_value = True

        args = self._make_args()
        result = _ensure_bed_role(args, mock_conn)

        self.assertTrue(result)
        mock_db.rolexists.assert_called_once_with(args, BED_ROLE, conn=mock_conn)
        mock_db.createrol.assert_called_once_with(
            args,
            BED_ROLE,
            conn=mock_conn,
            superuser=False,
            login=True,
            createdb=False,
            createrole=False,
        )
        mock_db.manage_schema_priv.assert_called_once_with(
            args, "grant", "usage", "engine", BED_ROLE, conn=mock_conn
        )

    @patch("bed.startup.database")
    def test_ensure_bed_role_skips_when_exists(self, mock_db):
        """_ensure_bed_role skips creation when the bed role already exists."""
        from bed.startup import _ensure_bed_role, BED_ROLE

        mock_conn = MagicMock()
        mock_db.rolexists.return_value = True
        mock_db.manage_schema_priv.return_value = True

        args = self._make_args()
        result = _ensure_bed_role(args, mock_conn)

        self.assertTrue(result)
        mock_db.rolexists.assert_called_once_with(args, BED_ROLE, conn=mock_conn)
        mock_db.createrol.assert_not_called()
        mock_db.manage_schema_priv.assert_called_once()

    @patch("bed.startup.database")
    def test_ensure_bed_role_fails_on_create_error(self, mock_db):
        """_ensure_bed_role returns False when createrol fails."""
        from bed.startup import _ensure_bed_role

        mock_conn = MagicMock()
        mock_db.rolexists.return_value = False
        mock_db.createrol.return_value = False

        args = self._make_args()
        result = _ensure_bed_role(args, mock_conn)

        self.assertFalse(result)
        mock_db.manage_schema_priv.assert_not_called()

    @patch("bed.startup.database")
    def test_ensure_bed_role_fails_on_schema_priv_error(self, mock_db):
        """_ensure_bed_role returns False when manage_schema_priv fails."""
        from bed.startup import _ensure_bed_role

        mock_conn = MagicMock()
        mock_db.rolexists.return_value = True
        mock_db.manage_schema_priv.return_value = False

        args = self._make_args()
        result = _ensure_bed_role(args, mock_conn)

        self.assertFalse(result)


class TestBedStartupMain(unittest.IsolatedAsyncioTestCase):
    """Test bed.startup.main end-to-end with mocked DB."""

    @patch("bed.startup.database")
    @patch("bed.startup.startuplib")
    @patch("bed.startup.bbsmodule")
    def test_main_runs_bbsengine6_startup_then_bed_role(
        self, mock_bbsmodule, mock_startuplib, mock_db
    ):
        """main() calls bbsengine6 startup, ensures the bed role, then
        dispatches to casino.startup.main."""
        from bed.startup import main, BED_ROLE

        mock_startuplib.runmodule.return_value = True
        mock_bbsmodule.runmodule.return_value = True
        mock_startuplib.buildargs.return_value = MagicMock()

        mock_pool = MagicMock()
        mock_db.getpool.return_value = mock_pool

        mock_conn = MagicMock()
        mock_db.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.connect.return_value.__exit__ = MagicMock(return_value=False)

        mock_db.rolexists.return_value = True
        mock_db.manage_schema_priv.return_value = True

        args = MagicMock(
            databasename="zoid6",
            databasehost="localhost",
            databaseport=5432,
        )
        result = main(args)

        self.assertTrue(result)
        mock_startuplib.runmodule.assert_called_once_with(args, "main")
        mock_bbsmodule.runmodule.assert_called_once_with(args, "casino.startup.main")
        mock_db.rolexists.assert_called_with(args, BED_ROLE, conn=mock_conn)
        mock_conn.commit.assert_called_once()

    @patch("bed.startup.database")
    @patch("bed.startup.startuplib")
    def test_main_returns_false_when_startup_fails(self, mock_startuplib, mock_db):
        """main() returns False when bbsengine6 startup fails."""
        from bed.startup import main

        mock_startuplib.runmodule.return_value = False
        mock_startuplib.buildargs.return_value = MagicMock()

        args = MagicMock()
        result = main(args)

        self.assertFalse(result)
        mock_db.getpool.assert_not_called()

    @patch("bed.startup.database")
    @patch("bed.startup.startuplib")
    def test_main_rolls_back_on_role_failure(self, mock_startuplib, mock_db):
        """main() rolls back when bed role creation fails."""
        from bed.startup import main

        mock_startuplib.runmodule.return_value = True
        mock_startuplib.buildargs.return_value = MagicMock()

        mock_pool = MagicMock()
        mock_db.getpool.return_value = mock_pool

        mock_conn = MagicMock()
        mock_db.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.connect.return_value.__exit__ = MagicMock(return_value=False)

        mock_db.rolexists.return_value = False
        mock_db.createrol.return_value = False

        args = MagicMock(databasename="zoid6")
        result = main(args)

        self.assertFalse(result)
        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()


class TestBedStartupStaysRunning(unittest.IsolatedAsyncioTestCase):
    """Test that BED starts up and stays running."""

    async def test_bed_server_starts_and_stays(self):
        """BED WebSocket server starts and remains running."""
        from bbsengine6.net import WebSocketServer
        from bbsengine6.net.defaultrouter import DefaultRouter

        host = "127.0.0.1"
        port = 18773

        server = WebSocketServer(host=host, port=port)
        router = DefaultRouter(MagicMock())
        router.register_all(server)

        await server.start()
        self.assertTrue(server.is_running)

        await asyncio.sleep(0.1)
        self.assertTrue(server.is_running)

        await server.stop()
        self.assertFalse(server.is_running)

    async def test_bed_handles_multiple_connections(self):
        """BED server handles multiple concurrent WebSocket connections."""
        import websockets
        from bbsengine6.net import WebSocketServer
        from bbsengine6.net.defaultrouter import DefaultRouter

        host = "127.0.0.1"
        port = 18774

        server = WebSocketServer(host=host, port=port)
        router = DefaultRouter(MagicMock())
        router.register_all(server)

        await server.start()

        connections = []
        try:
            for _ in range(3):
                ws = await websockets.connect(f"ws://{host}:{port}/")
                connections.append(ws)

            for ws in connections:
                self.assertEqual(ws.state.name, "OPEN")
        finally:
            for ws in connections:
                await ws.close()
            await server.stop()

    async def test_bed_survives_client_disconnect(self):
        """BED server continues running after a client disconnects."""
        import websockets
        from bbsengine6.net import WebSocketServer
        from bbsengine6.net.defaultrouter import DefaultRouter

        host = "127.0.0.1"
        port = 18775

        server = WebSocketServer(host=host, port=port)
        router = DefaultRouter(MagicMock())
        router.register_all(server)

        await server.start()

        ws = await websockets.connect(f"ws://{host}:{port}/")
        self.assertTrue(server.is_running)

        await ws.close()
        await asyncio.sleep(0.1)

        self.assertTrue(server.is_running)
        await server.stop()

    async def test_bed_ping_pong_while_running(self):
        """BED responds to ping while running, confirming it stays alive."""
        import json
        import websockets
        from bbsengine6.net import WebSocketServer
        from bbsengine6.net.defaultrouter import DefaultRouter

        host = "127.0.0.1"
        port = 18776

        server = WebSocketServer(host=host, port=port)
        router = DefaultRouter(MagicMock())
        router.register_all(server)

        await server.start()

        try:
            async with websockets.connect(f"ws://{host}:{port}/") as ws:
                await ws.send(json.dumps({"type": "ping"}))
                response = json.loads(await ws.recv())
                self.assertEqual(response["type"], "pong")

                await asyncio.sleep(0.1)
                self.assertTrue(server.is_running)

                await ws.send(json.dumps({"type": "ping"}))
                response = json.loads(await ws.recv())
                self.assertEqual(response["type"], "pong")
        finally:
            await server.stop()

    async def test_bed_full_startup_lifecycle(self):
        """Full BED startup lifecycle: start -> connect -> ping -> stop."""
        import json
        import websockets
        from bbsengine6.net import WebSocketServer
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        host = "127.0.0.1"
        port = 18777

        args = MagicMock(
            host=host,
            port=port,
            databasename="test",
            databasehost="localhost",
            databaseport=5432,
            databaseuser="test",
            databasepassword="test",
            debug=False,
        )

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(
            return_value=MagicMock()
        )
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)
        args.pool = mock_pool

        bed = BED(args, DefaultRouter)

        bed.server = WebSocketServer(host=host, port=port)
        bed.router = DefaultRouter(args)
        bed.router.register_all(bed.server)

        await bed.server.start()
        self.assertTrue(bed.server.is_running)

        async with websockets.connect(f"ws://{host}:{port}/") as ws:
            await ws.send(json.dumps({"type": "ping"}))
            response = json.loads(await ws.recv())
            self.assertEqual(response["type"], "pong")

        await bed.server.stop()
        self.assertFalse(bed.server.is_running)


if __name__ == "__main__":
    unittest.main()
