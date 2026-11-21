gradle.beforeSettings {
    if (settingsDir.toPath().endsWith("packages/flutter_tools/gradle")) {
        pluginManagement.repositories {
            mavenCentral()
            gradlePluginPortal()
        }
    }
}
