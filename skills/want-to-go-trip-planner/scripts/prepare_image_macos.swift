#!/usr/bin/env swift

import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("\(message)\n".utf8))
    exit(1)
}

guard CommandLine.arguments.count == 3 else {
    fail("usage: prepare_image_macos.swift INPUT OUTPUT")
}

let script = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent()
    .appendingPathComponent("prepare_display_image.py")

guard FileManager.default.fileExists(atPath: script.path) else {
    fail("prepare_display_image.py is missing")
}

let process = Process()
process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
process.arguments = [
    "python3",
    script.path,
    CommandLine.arguments[1],
    CommandLine.arguments[2],
]
process.standardOutput = FileHandle.standardOutput
process.standardError = FileHandle.standardError

do {
    try process.run()
    process.waitUntilExit()
} catch {
    fail("unable to run smart display-image preparation: \(error.localizedDescription)")
}

exit(process.terminationStatus)
