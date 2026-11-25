# Finanzas para Android

Proyecto Android estándar (Gradle + `gradlew`) que empaqueta **la misma aplicación** que sirve el contenedor: la
interfaz de `../app/static` en un WebView y el backend Python de `../app` ejecutándose dentro del móvil con
[Chaquopy](https://chaquo.com/chaquopy/). No hay servidor, puerto abierto ni permiso de Internet.

## Compilar

Requisitos: JDK 17 o superior y el Android SDK (`ANDROID_HOME`, o `sdk.dir` en `local.properties`). **No hace falta
Python ni Node** en la máquina de build, pero sí acceso a la red (Maven Central, Google Maven y PyPI).

```bash
cd android
./gradlew assembleDebug      # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease    # app/build/outputs/apk/release/app-release-unsigned.apk (sin firmar)
./gradlew lintDebug
```

Versiones: Gradle 9.9.2, Android Gradle Plugin 9.4.0 (la última que admite Android Studio), Chaquopy 17.2.0 con Python 3.12, `compileSdk` 37,
`targetSdk` 36, `minSdk` 26. Solo ABIs de 64 bits (`arm64-v8a`, `x86_64`): Python 3.12 en Chaquopy no tiene 32 bits.

## Cómo está montado

```
android/app/
├── build.gradle.kts            tareas que traen ../app al APK en cada build (no hay copias a mano)
└── src/main/
    ├── AndroidManifest.xml     sin INTERNET; sin copias de seguridad automáticas de los datos
    ├── java/es/finanzas/app/
    │   ├── MainActivity.java   WebView, canal de mensajes con Python, selector de ficheros, guardar CSV, atrás
    │   └── WebAssets.java      sirve app/static desde los assets con las cabeceras de seguridad del backend
    ├── assets/android-bridge.js  desvía fetch("/api/...") de la interfaz a Python
    └── python/
        ├── finanzas_bridge.py  start(data_dir) y handle(petición JSON) → respuesta JSON
        ├── fastapi/, pydantic/, starlette/   sustitutos mínimos (ver abajo)
```

En cada build, Gradle:

1. `syncWebAssets`: copia `../app/static` a los assets, bajo `public/`.
2. `syncPythonApp`: copia los `.py` de `../app` para que Chaquopy los empaquete como el paquete `app`.
3. `vendorPythonWheels`: descarga de PyPI openpyxl, et-xmlfile, xlrd y defusedxml (Python puro, versiones fijadas y
   SHA-256 comprobado) y los descomprime. Así Chaquopy no necesita `pip` ni `buildPython`.

Flujo de una petición: la interfaz llama a `api()` → `fetch("/api/...")` → `android-bridge.js` la convierte en un
mensaje JSON (los ficheros subidos van en base64) → `WebViewCompat.addWebMessageListener`, que solo acepta el origen
`https://appassets.androidplatform.net` → hilo único de Python → `finanzas_bridge.handle()` → `app.main.app.handle()`
→ respuesta de vuelta al `fetch`. La base de datos vive en el almacenamiento privado de la app (`files/data/finanzas.db`).

## Por qué hay un sustituto de FastAPI y Pydantic

FastAPI necesita Pydantic 2, que depende de `pydantic-core` (Rust), y Chaquopy no lo publica para Android (no está en
su repositorio de paquetes). En vez de cambiar `app/main.py`, `src/main/python` trae un `fastapi`, `pydantic` y
`starlette` mínimos con **solo lo que usa `main.py`** y el mismo comportamiento observable: códigos de estado, errores
`{"detail": ...}`, validación de parámetros, dependencias con `yield`, middleware y `lifespan`. Si `main.py` empieza a
usar algo que no está implementado, el sustituto falla de forma explícita.

Para comprobar que sigue siendo fiel, la suite completa de `tests/` se pasa contra él, sin FastAPI instalado:

```bash
sh android/scripts/test-python.sh     # desde la raíz; usa podman o docker
```

Si un cambio en el contenedor rompe esa prueba, hay que ampliar el sustituto.

## Qué queda por hacer

- Probar en un dispositivo o emulador real: arranque de Python, importación desde el selector de ficheros, exportar CSV,
  botón atrás, tema oscuro y teclado.
- Firma de release (keystore). El Jenkinsfile (`android/Jenkinsfile`, «Script Path» del job) ya compila esta carpeta.
- Adaptar algunos textos pensados para escritorio («tu ordenador», «el contenedor»).
- La primera ejecución compila el Python a bytecode en el móvil (`pyc { src = false }`), lo que hace más lento ese
  primer arranque. Si la máquina de build tiene Python 3.12, se puede quitar esa opción para compilarlo durante el build.
