plugins {
    id("java-library")
    idea
}

group = "dev.sbs"
version = "0.1.0"

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(21))
    }
}

repositories {
    mavenCentral()
    maven(url = "https://central.sonatype.com/repository/maven-snapshots")
    maven(url = "https://jitpack.io")
}

dependencies {
    // Simplified Annotations
    annotationProcessor(libs.simplified.annotations)

    // Lombok
    compileOnly(libs.lombok)
    annotationProcessor(libs.lombok)
    testCompileOnly(libs.lombok)
    testAnnotationProcessor(libs.lombok)

    // Tests
    testImplementation(libs.hamcrest)
    testImplementation(libs.junit.jupiter.api)
    testRuntimeOnly(libs.junit.jupiter.engine)
    testImplementation(libs.junit.platform.launcher)

    // Sibling API modules (composite-build substitutes by project name)
    api("com.github.simplified-api:skyblock") { version { strictly("ca66de7") } }

    // Simplified Libraries (github.com/simplified-dev)
    api("com.github.simplified-dev:collections") { version { strictly("7699a31") } }
    api("com.github.simplified-dev:utils") { version { strictly("036cc09") } }
    api("com.github.simplified-dev:reflection") { version { strictly("33b2f05") } }
    api("com.github.simplified-dev:gson-extras") { version { strictly("6421324") } }
    api("com.github.simplified-dev:persistence") { version { strictly("d2ee7b4") } }
    api("com.github.simplified-dev:client") { version { strictly("e9dbd5d") } }
    api("com.github.simplified-dev:expression") { version { strictly("f8e993c") } }

    // Minecraft-Library (github.com/minecraft-library)
    api("com.github.minecraft-library:text") { version { strictly("117775e") } }
    api("com.github.minecraft-library:nbt-factory") { version { strictly("f8b5f52") } }

    // Gson - @GsonType-annotated inner classes plus direct Deserializer/TypeAdapter usage
    api(libs.gson)
}

idea {
    module {
        excludeDirs.addAll(listOf(
            layout.projectDirectory.dir(".schema").asFile
        ))
    }
}

tasks {
    test {
        useJUnitPlatform()
    }
}
