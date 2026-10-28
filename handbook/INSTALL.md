# bed: installing the daemon

This is the operator guide for running bed on a host. It covers the OS account, the Python
venv, the systemd unit, the database login for the rollback path, and how to check it works.

bed is the WebSocket daemon. It runs as the OS user `bed`, from the venv at `/home/bed/venv`
(or the active venv, see section 3). zoid6 runs on bed with its own config, as its own account
and venv. See `zoid6/handbook/INSTALL.md` for that.

## Conventions

- Chain install steps with `&&` on one line, so a failed step stops the chain.
- Steps marked **root** need `sudo`. Steps marked **bed** run as the `bed` account, with
  `sudo -u bed`. Anything else runs as the operator.
- The install targets call `sudo` internally. Run them as the operator, not as root.
  Running `make` directly as root creates files owned by root in the build tree.

## 1. Prerequisites

- PostgreSQL with the `bed` and `bbsengine6` schemas installed (section 6).
- Python 3.12 (`python3.12` on PATH). `make`, `rsync`, and `sudo`.
- systemd, with `systemd-sysusers` and `systemd-tmpfiles`.
- SELinux, if enforcing (section 3 covers the relabel).

## 2. The OS account

The `bed` account is created by `systemd-sysusers` from `src/bed/daemon/bed.sysusers`. The
`install-sysusers` target does it, so the account has no separate `useradd` step.

**Check the account** (operator):

```
getent passwd bed
```

Expected: home `/home/bed`, shell `/usr/sbin/nologin`. If the home is `/var/lib/bed`, the account
predates the move; change it with **root**:

```
sudo mkdir -p /home/bed && sudo usermod -d /home/bed bed && sudo chown bed:bed /home/bed
```

## 3. The venv

The venv location is resolved in this order, the same in `make` and deploytool:

1. `--prefix PATH` (deploytool), which sets `DEPLOY_PREFIX`. Wins over everything.
2. The active venv (`VIRTUAL_ENV`). Without `--prefix`, the venv you run the install from is used.
3. `/home/bed/venv`, only when no venv is active.

Check what will be used, without installing anything (operator):

```
make -s -C bed print-venv-dir
```

**Create the venv and install bed** (operator, from the active venv):

```
make -C bed install-venv
```

`install-venv` creates the venv as the `bed` account if it is missing, installs the bbsengine6
and bed wheels into it, and relabels the venv `bin/` for SELinux. It needs `sudo`.

To install into a specific venv, pass the prefix through deploytool, or set it on the command line:

```
deploy bed.venv --prefix /home/bed/venv
make -C bed install-venv VENV_DIR=/home/bed/venv
```

**Check the venv** (operator):

```
/home/bed/venv/bin/python -c "import bed, bed._version as v; print(v.__version__)"
```

If `status=203/EXEC` appears later with "Permission denied", the venv `bin/` is not labelled for
SELinux. Relabel it with **root**:

```
sudo make -C bed restorecon VENV_DIR=/home/bed/venv
```

## 4. The systemd unit

`install-systemd` renders `src/bed/daemon/bed.service`, substituting the venv path for
`@VENV_DIR@`, and installs it under `/usr/lib/systemd/system/`. Run it as the operator; it uses
`sudo` internally:

```
make -C bed install-systemd
```

The unit runs as `bed` from `/home/bed/venv/bin/bed`. It sets `ProtectHome=read-only` and makes
`/home/bed` writable, so the daemon can read its own venv. A `ProtectHome=yes` unit would hide
`/home` and fail with `203/EXEC`.

**Check the rendered unit** (operator):

```
systemctl cat bed | grep ExecStart
systemd-analyze verify /usr/lib/systemd/system/bed.service
```

`ExecStart` should show the venv path you installed into.

## 5. Configuration

bed reads nothing from `/etc`. Its configuration is the `bed.json` packaged inside the wheel, so
the install needs no configuration step and writes no file outside the venv. This matters on a
read-only root filesystem, where an `/etc` write fails.

To run with a different configuration, pass it on the command line or in the environment:

- `--config PATH` on the daemon command line, or
- `BED_CONFIG=PATH` in the environment.

`install-venv` refuses to install a wheel that does not contain a valid packaged `bed.json`. This
is the configuration check: a wheel without its configuration is a broken build, and would start a
daemon with nothing to run.

Check the packaged configuration (operator):

```
python3 -c "import json, bed.config as c; print(json.load(open(c.get_package_data_path('bed.json')))['bed'])"
```

Set `BED_DATABASEUSER` only through the environment or `--databaseuser`, if the daemon must log in
as a role other than the default. See section 6.

## 5a. The account home (`BED_HOME`)

`BED_HOME` is the one variable for the `bed` account's home. It sets the sysusers home, the
tmpfiles state directory, the unit's `WorkingDirectory` and `ReadWritePaths`, and the default venv
(`$(BED_HOME)/venv`). Change it in one place, or override it on the command line:

```
make -C bed install-sysusers install-tmpfiles BED_HOME=/home/NEEDINFO
```

Its value is not yet decided: the Makefiles currently default to `/home/NEEDINFO`, a placeholder.
Set the real path before the first install on a host.

## 6. The database

The daemon connects to PostgreSQL. The default login is `bed`, which is the rollback login.

**Bootstrap the database** (operator, after the venv exists):

```
/home/bed/venv/bin/bed-startup
```

`make -C bed setup-db` runs the same command from the active venv.

**Identd mapping for the rollback login** (root). This adds the managed lines to `pg_ident.conf`
and `pg_hba.conf`, backs both up, checks the server config, and reloads PostgreSQL:

```
sudo make -C bed/src install-pgsql
```

The managed lines are in `src/bed/daemon/pgsql/`. Remove them at cutover, together with the `bed`
role.

## 7. Start and check the service

Enable and start the daemon (operator, with sudo):

```
sudo systemctl enable --now bed
systemctl is-active bed
systemctl show bed -p NRestarts -p ExecMainStatus
```

Expected: `active`, `NRestarts=0`, `ExecMainStatus=0`.

If the unit stops after repeated failures (systemd's start limit, 10 restarts in 5 minutes):

```
sudo systemctl reset-failed bed && sudo systemctl restart bed
```

Read the journal to see why it failed (operator; the journal may need the `systemd-journal` group):

```
journalctl -u bed -n 50 --no-pager
```

## 8. Updating

To update bed in place, from the operator's active venv:

```
make -C bed deploy
```

`deploy` builds wheels for bbsengine6 and bed and installs them into the active venv. It does not
restart the service. Restart after an update:

```
sudo systemctl restart bed
```

For a development checkout, `make -C bed deploy EDITABLE=1` installs bed editable from `src/`.

## 9. Uninstalling

```
sudo make -C bed uninstall
```

This stops and removes the unit, the venv, and the tmpfiles and sysusers entries.
It does not remove the database role or the identd lines; remove those at cutover.

## Known gaps

- `BED_HOME` is a placeholder (`/home/NEEDINFO`) until the real path is chosen.
- The `bed` account home was changed by hand on minotaur. `BED_HOME` renders the sysusers entry, so
  a fresh host gets the right home as long as `BED_HOME` is set at install time.
