# Release Checklist

1. **Bump version** in two places:
   - `simdeck.py` → `__version__ = "X.X.X"`
   - `installer.iss` → `#define AppVersion "X.X.X"`

2. **Rebuild the exe:**
   ```
   pyinstaller --noconfirm SimDeck.spec
   ```
   Then copy assets into dist:
   ```
   copy simdeck.ico dist\SimDeck\simdeck.ico
   copy simdeck_button.png dist\SimDeck\simdeck_button.png
   copy simdeck_button@2x.png dist\SimDeck\simdeck_button@2x.png
   ```

3. **Build the installer:**
   ```
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
   ```
   Output: `installer_output\SimDeck-X.X.X-setup.exe`

4. **Commit, tag, push:**
   ```
   git add -A
   git commit -m "vX.X.X — describe what changed"
   git tag vX.X.X
   git push origin master --tags
   ```

5. **Create GitHub release:**
   ```
   gh release create vX.X.X "installer_output\SimDeck-X.X.X-setup.exe" --title "SimDeck vX.X.X" --notes "describe changes here"
   ```

Running users will see "Update available: vX.X.X" in the tray menu on next startup.
