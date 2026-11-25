import java.net.URI
import java.security.MessageDigest
import java.util.zip.ZipInputStream
import javax.inject.Inject

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// La aplicación de verdad vive en la raíz del repositorio (`app/`, la misma que sirve el contenedor). Aquí no se
// copia a mano: estas tareas la llevan al APK en cada build, así que los cambios del contenedor llegan solos.
val repoRoot: File = rootProject.projectDir.parentFile

android {
    namespace = "es.finanzas.app"
    compileSdk = 37

    defaultConfig {
        applicationId = "es.finanzas.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 3
        versionName = "1.3"
        ndk {
            // Python 3.12 en Chaquopy solo existe para 64 bits.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

/** Copia la interfaz web (`app/static`) a los assets, bajo `public/`. */
abstract class SyncWebAssets : DefaultTask() {
    @get:InputDirectory
    abstract val source: DirectoryProperty

    @get:OutputDirectory
    abstract val output: DirectoryProperty

    @get:Inject
    abstract val fs: FileSystemOperations

    @TaskAction
    fun run() {
        fs.sync {
            from(source)
            into(output.dir("public"))
        }
    }
}

/** Copia el código Python de `app/` (sin la interfaz ni cachés) para que Chaquopy lo empaquete. */
abstract class SyncPythonApp : DefaultTask() {
    @get:InputDirectory
    abstract val source: DirectoryProperty

    @get:OutputDirectory
    abstract val output: DirectoryProperty

    @get:Inject
    abstract val fs: FileSystemOperations

    @TaskAction
    fun run() {
        fs.sync {
            from(source) {
                include("**/*.py")
                exclude("**/__pycache__/**")
            }
            into(output.dir("app"))
        }
    }
}

/**
 * Descarga las dependencias Python (todas Python puro) desde PyPI, comprueba su SHA-256 y las descomprime.
 * Así no hace falta `pip` ni un Python en la máquina de build: basta con Gradle y acceso a la red.
 */
abstract class VendorPythonWheels : DefaultTask() {
    @get:Input
    abstract val wheels: MapProperty<String, String> // url -> sha256

    @get:OutputDirectory
    abstract val output: DirectoryProperty

    @TaskAction
    fun run() {
        val target = output.get().asFile
        target.deleteRecursively()
        target.mkdirs()
        for ((url, expected) in wheels.get()) {
            val bytes = URI(url).toURL().openStream().use { it.readBytes() }
            val actual = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            check(actual == expected) { "SHA-256 incorrecto para $url: $actual" }
            ZipInputStream(bytes.inputStream()).use { zip ->
                while (true) {
                    val entry = zip.nextEntry ?: break
                    val top = entry.name.substringBefore('/')
                    if (entry.isDirectory || top.endsWith(".dist-info") || top.endsWith(".data")) continue
                    val file = target.resolve(entry.name).canonicalFile
                    check(file.path.startsWith(target.canonicalPath)) { "Ruta no válida en $url: ${entry.name}" }
                    file.parentFile.mkdirs()
                    file.outputStream().use { zip.copyTo(it) }
                }
            }
        }
    }
}

val syncWebAssets = tasks.register<SyncWebAssets>("syncWebAssets") {
    source.set(repoRoot.resolve("app/static"))
    output.set(layout.buildDirectory.dir("generated/finanzas/assets"))
}

val pythonAppDir: File = layout.buildDirectory.dir("generated/finanzas/python-app").get().asFile
val pythonLibsDir: File = layout.buildDirectory.dir("generated/finanzas/python-libs").get().asFile

val syncPythonApp = tasks.register<SyncPythonApp>("syncPythonApp") {
    source.set(repoRoot.resolve("app"))
    output.set(pythonAppDir)
}

// Mismas versiones que requirements.txt (más et-xmlfile, que necesita openpyxl). FastAPI, Pydantic y Starlette no
// se descargan: en Android los sustituye src/main/python. uvicorn y python-multipart no hacen falta sin servidor.
val vendorPythonWheels = tasks.register<VendorPythonWheels>("vendorPythonWheels") {
    wheels.set(mapOf(
        "https://files.pythonhosted.org/packages/c0/da/977ded879c29cbd04de313843e76868e6e13408a94ed6b987245dc7c8506/openpyxl-3.1.5-py2.py3-none-any.whl"
            to "5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2",
        "https://files.pythonhosted.org/packages/c1/8b/5fe2cc11fee489817272089c4203e679c63b570a5aaeb18d852ae3cbba6a/et_xmlfile-2.0.0-py3-none-any.whl"
            to "7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa",
        "https://files.pythonhosted.org/packages/1a/62/c8d562e7766786ba6587d09c5a8ba9f718ed3fa8af7f4553e8f91c36f302/xlrd-2.0.2-py2.py3-none-any.whl"
            to "ea762c3d29f4cca48d82df517b6d89fbce4db3107f9d78713e48cd321d5c9aa9",
        "https://files.pythonhosted.org/packages/07/6c/aa3f2f849e01cb6a001cd8554a88d4c77c5c1a31c95bdf1cf9301e6d9ef4/defusedxml-0.7.1-py2.py3-none-any.whl"
            to "a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61",
    ))
    output.set(pythonLibsDir)
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(syncWebAssets, SyncWebAssets::output)
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        // Compilar a .pyc exige un Python igual en la máquina de build. Sin él, se compila en el móvil la primera vez.
        pyc {
            src = false
            pip = false
        }
    }
    sourceSets {
        getByName("main") {
            srcDir(pythonAppDir)
            srcDir(pythonLibsDir)
        }
    }
}

// Chaquopy no deduce de quién dependen sus carpetas de código: se le indica a mano.
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }.configureEach {
    dependsOn(syncPythonApp, vendorPythonWheels)
}

dependencies {
    implementation("androidx.activity:activity:1.13.0")
    implementation("androidx.webkit:webkit:1.18.0")
}
