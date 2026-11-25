package es.finanzas.app;

import android.content.res.AssetManager;
import android.webkit.WebResourceResponse;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/**
 * Sirve la interfaz web desde los assets del APK (copiados de app/static en el build), con las mismas cabeceras de
 * seguridad que pone el backend en el contenedor. Las peticiones /api/... no llegan aquí: van por android-bridge.js.
 */
final class WebAssets {
    private static final String ROOT = "public";
    private static final String BRIDGE_PATH = "/android-bridge.js";
    private static final String MODULE_TAG = "<script type=\"module\"";
    private static final String CSP =
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";

    private static final Map<String, String> TYPES = new HashMap<>();

    static {
        TYPES.put("html", "text/html");
        TYPES.put("js", "text/javascript");
        TYPES.put("mjs", "text/javascript");
        TYPES.put("css", "text/css");
        TYPES.put("svg", "image/svg+xml");
        TYPES.put("json", "application/json");
        TYPES.put("png", "image/png");
        TYPES.put("ico", "image/x-icon");
        TYPES.put("woff2", "font/woff2");
    }

    private final AssetManager assets;

    WebAssets(AssetManager assets) {
        this.assets = assets;
    }

    WebResourceResponse open(String path) {
        if (path == null || path.isEmpty() || path.equals("/")) path = "/index.html";
        if (path.contains("..") || path.contains("\\") || path.startsWith("/api/")) return notFound();
        try {
            if (path.equals(BRIDGE_PATH)) return ok("text/javascript", assets.open(path.substring(1)));
            if (path.equals("/index.html")) return ok("text/html", new ByteArrayInputStream(indexWithBridge()));
            return ok(typeOf(path), assets.open(ROOT + path));
        } catch (IOException missing) {
            return notFound();
        }
    }

    /** index.html con el puente cargado antes que la aplicación: los scripts de módulo se ejecutan después. */
    private byte[] indexWithBridge() throws IOException {
        String html = new String(readAll(assets.open(ROOT + "/index.html")), StandardCharsets.UTF_8);
        int at = html.indexOf(MODULE_TAG);
        String tag = "<script src=\"" + BRIDGE_PATH + "\"></script>\n  ";
        html = at >= 0 ? html.substring(0, at) + tag + html.substring(at) : html.replace("</head>", tag + "</head>");
        return html.getBytes(StandardCharsets.UTF_8);
    }

    private static String typeOf(String path) {
        int dot = path.lastIndexOf('.');
        String type = dot >= 0 ? TYPES.get(path.substring(dot + 1).toLowerCase(java.util.Locale.ROOT)) : null;
        return type != null ? type : "application/octet-stream";
    }

    private static Map<String, String> headers() {
        Map<String, String> headers = new HashMap<>();
        headers.put("Content-Security-Policy", CSP);
        headers.put("X-Content-Type-Options", "nosniff");
        headers.put("Referrer-Policy", "no-referrer");
        headers.put("Cache-Control", "no-cache");
        return headers;
    }

    private static WebResourceResponse ok(String type, InputStream body) {
        String charset = type.startsWith("text/") || type.endsWith("json") || type.endsWith("svg+xml") ? "utf-8" : null;
        return new WebResourceResponse(type, charset, 200, "OK", headers(), body);
    }

    static WebResourceResponse notFound() {
        return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found", headers(),
                new ByteArrayInputStream(new byte[0]));
    }

    private static byte[] readAll(InputStream in) throws IOException {
        try (InputStream input = in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[16384];
            int read;
            while ((read = input.read(buffer)) != -1) out.write(buffer, 0, read);
            return out.toByteArray();
        }
    }
}
