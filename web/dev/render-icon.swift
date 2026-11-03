// The app's icons, drawn from the click wheel (assets/favicon.svg, in its light colours) into transparent PNGs:
//
//     swiftc dev/render-icon.swift -o /tmp/render-icon
//     /tmp/render-icon wheel-light.svg assets/app-icon.png 1024 100 shadow    the app: macOS proportions, with a shadow
//     /tmp/render-icon wheel-light.svg assets/apple-touch-icon.png 180 0
//
// wheel-light.svg is favicon.svg without its dark-mode rule. Quick Look can't do this: it draws on white.
import AppKit

let args = CommandLine.arguments, svg = URL(fileURLWithPath: args[1]), out = URL(fileURLWithPath: args[2])
let size = CGFloat(Double(args[3])!), inset = CGFloat(Double(args[4])!), shadow = args.count > 5
guard let wheel = NSImage(contentsOf: svg) else { fatalError("can't read \(svg.path)") }
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                           isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSColor.clear.set(); NSRect(x: 0, y: 0, width: size, height: size).fill()
if shadow {
    let s = NSShadow(); s.shadowOffset = NSSize(width: 0, height: -size * 0.016); s.shadowBlurRadius = size * 0.04; s.shadowColor = NSColor.black.withAlphaComponent(0.3); s.set()
}
let side = size - inset * 2
wheel.draw(in: NSRect(x: inset, y: inset + (shadow ? size * 0.012 : 0), width: side, height: side))  // lifted a little, as the shadow falls below
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: out)
