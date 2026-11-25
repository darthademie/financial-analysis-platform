package es.finanzas.app;

import android.annotation.SuppressLint;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.util.Log;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;
import android.webkit.RenderProcessGoneDetail;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.webkit.JavaScriptReplyProxy;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.OutputStream;
import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Una sola pantalla: un WebView con la interfaz de app/static y Python (Chaquopy) ejecutando app/main.py.
 *
 * <p>No hay servidor ni puerto abierto, y la app no pide permiso de Internet: las páginas salen de los assets y
 * cada petición /api/... llega por un canal de mensajes que solo acepta el origen propio de la app.
 */
public class MainActivity extends ComponentActivity {
    private static final String TAG = "Finanzas";
    static final String HOST = "appassets.androidplatform.net";
    static final String ORIGIN = "https://" + HOST;
    private static final int PAGE_LIGHT = Color.rgb(0xf6, 0xf5, 0xf1); // --page en styles.css
    private static final int PAGE_DARK = Color.rgb(0x0f, 0x0f, 0x0e);

    /** Python se usa siempre desde este hilo: la aplicación no está pensada para peticiones simultáneas. */
    private static final ExecutorService PYTHON = Executors.newSingleThreadExecutor();
    private static PyObject bridge;
    private static String startError;

    private final Handler mainThread = new Handler(Looper.getMainLooper());
    private FrameLayout root;
    private WebView webView;
    private WebAssets webAssets;
    private ValueCallback<Uri[]> fileCallback;
    private byte[] pendingSave;

    private final ActivityResultLauncher<Intent> openFile = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                if (fileCallback == null) return;
                fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.getResultCode(), result.getData()));
                fileCallback = null;
            });

    private final ActivityResultLauncher<Intent> saveFile = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                byte[] content = pendingSave;
                pendingSave = null;
                Intent data = result.getData();
                if (result.getResultCode() != RESULT_OK || data == null || data.getData() == null || content == null) return;
                writeFile(data.getData(), content);
            });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        root = new FrameLayout(this);
        setContentView(root);
        setUpSystemBars(isNightMode());

        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            TextView message = new TextView(this);
            message.setPadding(48, 48, 48, 48);
            message.setText(R.string.webview_too_old);
            root.addView(message);
            return;
        }

        if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
        startPython(new File(getFilesDir(), "data").getAbsolutePath());

        webAssets = new WebAssets(getAssets());
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        configureWebView();
        setUpBackNavigation();

        if (savedInstanceState == null || webView.restoreState(savedInstanceState) == null) {
            webView.loadUrl(ORIGIN + "/index.html");
        }
    }

    // ------------------------------------------------------------------ Python

    private static void startPython(String dataDir) {
        PYTHON.execute(() -> {
            if (bridge != null) return;
            try {
                PyObject module = Python.getInstance().getModule("finanzas_bridge");
                module.callAttr("start", dataDir);
                bridge = module;
            } catch (Throwable error) {
                startError = String.valueOf(error);
                Log.e(TAG, "No se ha podido iniciar Python", error);
            }
        });
    }

    @SuppressLint("RequiresFeature")
    private void handleRequest(String message, JavaScriptReplyProxy reply) {
        PYTHON.execute(() -> {
            String answer;
            if (bridge == null) {
                answer = errorAnswer(message, "No se ha podido iniciar la aplicación: " + startError);
            } else {
                try {
                    answer = bridge.callAttr("handle", message).toString();
                } catch (Throwable error) {
                    Log.e(TAG, "Error en Python", error);
                    answer = errorAnswer(message, "Error inesperado en la aplicación.");
                }
            }
            String finalAnswer = answer;
            mainThread.post(() -> reply.postMessage(finalAnswer));
        });
    }

    private static String errorAnswer(String message, String detail) {
        try {
            JSONObject request = new JSONObject(message);
            JSONObject headers = new JSONObject().put("content-type", "application/json");
            JSONObject body = new JSONObject().put("detail", detail);
            return new JSONObject().put("id", request.opt("id")).put("status", 500).put("headers", headers)
                    .put("text", body.toString()).toString();
        } catch (JSONException impossible) {
            return "{}";
        }
    }

    // ------------------------------------------------------------------ WebView

    // WEB_MESSAGE_LISTENER se comprueba en onCreate antes de llegar aquí.
    @SuppressLint({"SetJavaScriptEnabled", "RequiresFeature"})
    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true); // preferencias de la interfaz en localStorage
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        WebView.setWebContentsDebuggingEnabled((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0);

        WebViewCompat.addWebMessageListener(webView, "FinanzasBridge", Collections.singleton(ORIGIN),
                (view, message, sourceOrigin, isMainFrame, reply) -> {
                    String data = message.getData();
                    if (!isMainFrame || data == null) return;
                    onBridgeMessage(data, reply);
                });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                // Nada sale a la red: lo que no es de la app no se carga.
                if (!"https".equals(url.getScheme()) || !HOST.equals(url.getHost())) return WebAssets.notFound();
                return webAssets.open(url.getPath());
            }

            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                // Si el sistema cierra el proceso del WebView (falta de memoria), se vuelve a crear la pantalla.
                Log.w(TAG, "El proceso del WebView ha terminado; se recrea la actividad");
                if (view == webView) {
                    root.removeView(webView);
                    webView.destroy();
                    webView = null;
                }
                recreate();
                return true;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if (HOST.equals(url.getHost())) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, url));
                } catch (ActivityNotFoundException ignored) {
                    // Sin aplicación para abrirlo: se ignora.
                }
                return true;
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                // Sin filtrar por tipo: los bancos exportan «falsos Excel» y el importador mira el contenido.
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                        .addCategory(Intent.CATEGORY_OPENABLE)
                        .setType("*/*");
                try {
                    openFile.launch(intent);
                } catch (ActivityNotFoundException error) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
    }

    private void onBridgeMessage(String data, JavaScriptReplyProxy reply) {
        JSONObject message;
        try {
            message = new JSONObject(data);
        } catch (JSONException error) {
            return;
        }
        switch (message.optString("kind")) {
            case "request":
                handleRequest(data, reply);
                break;
            case "save":
                saveFile(message.optString("name", "descarga"), message.optString("mime", "application/octet-stream"),
                        message.optString("data"));
                break;
            case "theme":
                setUpSystemBars(message.optBoolean("dark"));
                break;
            default:
                Log.w(TAG, "Mensaje desconocido del WebView");
        }
    }

    // ------------------------------------------------------------------ ficheros

    private void saveFile(String name, String mime, String base64) {
        pendingSave = Base64.decode(base64, Base64.DEFAULT);
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType(mime.split(";")[0].trim())
                .putExtra(Intent.EXTRA_TITLE, name);
        try {
            saveFile.launch(intent);
        } catch (ActivityNotFoundException error) {
            pendingSave = null;
            Toast.makeText(this, R.string.save_unavailable, Toast.LENGTH_LONG).show();
        }
    }

    private void writeFile(Uri target, byte[] content) {
        try (OutputStream out = getContentResolver().openOutputStream(target)) {
            if (out == null) throw new java.io.IOException("Sin destino");
            out.write(content);
            Toast.makeText(this, R.string.file_saved, Toast.LENGTH_SHORT).show();
        } catch (Exception error) {
            Log.e(TAG, "No se ha podido guardar el fichero", error);
            Toast.makeText(this, R.string.save_failed, Toast.LENGTH_LONG).show();
        }
    }

    // ------------------------------------------------------------------ navegación y barras del sistema

    private void setUpBackNavigation() {
        // La interfaz navega por #/ruta, así que el historial del WebView es el de las secciones visitadas.
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (webView != null && webView.canGoBack()) {
                    webView.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });
    }

    private boolean isNightMode() {
        return (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
    }

    /** La página ocupa toda la pantalla y se aparta de barras, muesca y teclado con márgenes. */
    @SuppressWarnings("deprecation")
    private void setUpSystemBars(boolean dark) {
        Window window = getWindow();
        root.setBackgroundColor(dark ? PAGE_DARK : PAGE_LIGHT);
        window.setStatusBarColor(Color.TRANSPARENT);
        window.setNavigationBarColor(Color.TRANSPARENT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false);
            WindowInsetsController controller = window.getInsetsController();
            if (controller != null) {
                int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                controller.setSystemBarsAppearance(dark ? 0 : light, light);
            }
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                Insets ime = insets.getInsets(WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime.bottom));
                return WindowInsets.CONSUMED;
            });
        } else {
            int flags = View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION;
            if (!dark) flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            window.getDecorView().setSystemUiVisibility(flags);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                        insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
                return insets.consumeSystemWindowInsets();
            });
        }
        root.requestApplyInsets();
    }

    // ------------------------------------------------------------------ ciclo de vida

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (webView != null) webView.saveState(outState);
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            root.removeView(webView);
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
