# Release Checklist

1. **Bump version** in two places:
   - `simdeck.py` → `__version__ = "X.X.X"`
   - `installer.iss` → `#define AppVersion "X.X.X"`

2. **Commit and push:**
   ```
   git add simdeck.py installer.iss
   git commit -m "vX.X.X — describe what changed"
   git push origin master
   ```

3. **Build the exe** (assets are bundled automatically via `SimDeck.spec`):
   ```
   pyinstaller SimDeck.spec --clean -y
   ```

4. **Build the installer:**
   ```
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
   ```
   Output: `installer_output\SimDeck-X.X.X-setup.exe`

5. **Tag and push:**
   ```
   git tag vX.X.X
   git push origin vX.X.X
   ```

6. **Create GitHub release:**
   ```
   gh release create vX.X.X "installer_output\SimDeck-X.X.X-setup.exe" --title "SimDeck vX.X.X" --notes "describe changes here"
   ```

Users on v1.1.1+ will see "Update to vX.X.X" in Settings and the tray menu on next startup. Clicking it downloads and installs automatically.
