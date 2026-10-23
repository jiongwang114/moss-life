import java.util.Properties
import groovy.json.JsonSlurper

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.moss.mobile"
    compileSdk = 35

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.moss.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 17
        versionName = "0.3.7"
        testInstrumentationRunner = "com.moss.mobile.WebViewVerification"

        buildConfigField("String", "API_BASE_URL", "\"https://note.jiongplay.cn/api/v1\"")
    }

    val signingFile = System.getenv("MOSS_SIGNING_PROPERTIES")?.let { file(it) }
    val personalSigning = if (signingFile?.isFile == true) {
        val values = Properties().apply { signingFile.reader(Charsets.UTF_8).use { load(it) } }
        signingConfigs.create("personal") {
            storeFile = file(values.getProperty("storeFile"))
            storePassword = values.getProperty("storePassword")
            keyAlias = values.getProperty("keyAlias")
            keyPassword = values.getProperty("keyPassword")
        }
    } else null

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = personalSigning
        }
    }

    buildFeatures {
        buildConfig = true
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.10.0")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    implementation("androidx.room:room-runtime:2.7.0")
    implementation("androidx.room:room-ktx:2.7.0")
    ksp("androidx.room:room-compiler:2.7.0")

}

// Convert every packaged script (including vendors) before every Android build.
// npm ci in moss-prototype prepares the pinned build-time dependencies.
val frontend = rootProject.file("../../moss-prototype")
val buildCompatFrontend by tasks.registering(Exec::class) {
    workingDir(frontend)
    inputs.files(fileTree(frontend) {
        include("*.js", "*.html", "*.css", "assets.json", "package*.json", "build-android.cjs", "android-support/**", "vendor/**")
    })
    outputs.dir(layout.buildDirectory.dir("compat-frontend"))
    commandLine("node", frontend.resolve("build-android.cjs").absolutePath,
        layout.buildDirectory.dir("compat-frontend").get().asFile.absolutePath)
}
val syncFrontend by tasks.registering(Sync::class) {
    dependsOn(buildCompatFrontend)
    doFirst {
        val compiled = layout.buildDirectory.dir("compat-frontend").get().asFile
        val html = compiled.resolve("index.html").readText(Charsets.UTF_8)
        val allowed = (JsonSlurper().parse(compiled.resolve("assets.json")) as List<*>).map { it as String }.toSet()
        val referenced = Regex("(?:src|href)=\"([^\"]+)\"").findAll(html)
            .map { it.groupValues[1] }.filter { !it.contains(":") && !it.startsWith("#") }.toSet()
        check(allowed.containsAll(referenced)) { "HTML assets blocked by Android host: ${referenced - allowed}" }
        check(referenced.all { compiled.resolve(it).isFile }) { "A referenced frontend asset is missing" }
    }
    from(layout.buildDirectory.dir("compat-frontend"))
    into(layout.buildDirectory.dir("generated/moss-assets/web"))
}
android.sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/moss-assets"))
tasks.named("preBuild") { dependsOn(syncFrontend) }

