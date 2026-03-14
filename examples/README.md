# Examples

Each example is a self-contained CMake project in its own directory.

## 1. Simple (`1-simple/`)

Basic usage with local counter and auto-generated `version.h`.

```bash
cd 1-simple
cmake -B build
cmake --build build
./build/simple_example        # Linux/Mac
.\build\Debug\simple_example.exe  # Windows
```

## 2. With Server (`2-with-server/`)

Synchronized build numbers via central server.

Start the server first:
```bash
python ../../src/server.py --accept-unknown
```

Then build:
```bash
cd 2-with-server
cmake -B build
cmake --build build
./build/server_example
```

Falls back to local counter automatically if server is unavailable.

## 3. Custom Location (`3-custom-location/`)

Stores the counter file in the source directory instead of the build directory. Useful if you want to commit the counter to version control.

```bash
cd 3-custom-location
cmake -B build
cmake --build build
cat my_build_counter.txt  # see the counter
```

## 4. Configure Mode (`4-configure-mode/`)

Build number embedded in `project(VERSION ...)` at configure time. Useful when you need the version in RC files, `configure_file()` templates, or CPack — not just a header.

```bash
cd 4-configure-mode
cmake -B build
cmake --build build         # reconfigures and increments automatically
cmake --build build         # increments again
.\build\Debug\configure_example.exe  # Windows
./build/configure_example            # Linux/Mac
```

## Tips

- Each CMakeLists.txt is self-contained — copy any example as a starting point
- Project keys should be unique across different projects
- Build numbers increment on every `cmake --build` in both modes
- Server is optional — all examples work without it (local fallback)
- Counter files are plain text and human-readable
