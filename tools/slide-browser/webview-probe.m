// macOS WKWebView parity probe. Uses an ephemeral data store and synthetic capture input.
#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>

static WKWebView *web;
static NSString *output;
static NSInteger count;
static void fail(NSError *error) { fprintf(stderr, "WKWebView probe: %s\n", error.description.UTF8String); exit(1); }
static void capture(NSInteger index) {
    if (index >= count) exit(0);
    NSString *script = [NSString stringWithFormat:@"window.scrollTo(0,%ld); await new Promise(r=>setTimeout(r,250)); return true;", (long)index*720];
    [web callAsyncJavaScript:script arguments:@{} inFrame:nil inContentWorld:WKContentWorld.pageWorld completionHandler:^(id value, NSError *error) {
        if (error) fail(error);
        WKSnapshotConfiguration *config = [WKSnapshotConfiguration new];
        config.rect = NSMakeRect(0,0,1280,720);
        config.snapshotWidth = @1280;
        config.afterScreenUpdates = NO;
        [web takeSnapshotWithConfiguration:config completionHandler:^(NSImage *image, NSError *shotError) {
            if (shotError) fail(shotError);
            NSBitmapImageRep *bitmap = [NSBitmapImageRep imageRepWithData:image.TIFFRepresentation];
            NSData *png = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
            NSString *path = [output stringByAppendingPathComponent:[NSString stringWithFormat:@"slide-%02ld.png", (long)index+1]];
            if (![png writeToFile:path atomically:YES]) exit(4);
            capture(index+1);
        }];
    }];
}
@interface Probe : NSObject <WKNavigationDelegate>
@end
@implementation Probe
- (void)webView:(WKWebView *)view didFinishNavigation:(WKNavigation *)navigation {
    fprintf(stderr,"WKWebView: loaded renderer\n");
    NSString *script = @"const end=Date.now()+20000; while(!window.guntherCapture&&Date.now()<end) await new Promise(r=>setTimeout(r,50)); return await window.guntherCapture.ready();";
    [view callAsyncJavaScript:script arguments:@{} inFrame:nil inContentWorld:WKContentWorld.pageWorld completionHandler:^(id value, NSError *error) {
        if (error) fail(error);
        fprintf(stderr,"WKWebView: renderer ready\n");
        NSError *encodingError = nil;
        NSData *json = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingPrettyPrinted|NSJSONWritingSortedKeys error:&encodingError];
        if (encodingError) fail(encodingError);
        count = [value[@"slides"] count];
        [json writeToFile:[output stringByAppendingPathComponent:@"webview.json"] atomically:YES];
        capture(0);
    }];
}
- (void)webView:(WKWebView *)view didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error { fail(error); }
- (void)webView:(WKWebView *)view didFailNavigation:(WKNavigation *)navigation withError:(NSError *)error { fail(error); }
@end
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 4) return 2;
        output = [NSString stringWithUTF8String:argv[3]];
        [[NSFileManager defaultManager] createDirectoryAtPath:output withIntermediateDirectories:YES attributes:nil error:nil];
        NSApplication *app = NSApplication.sharedApplication;
        [app setActivationPolicy:NSApplicationActivationPolicyProhibited];
        [app finishLaunching];
        WKWebViewConfiguration *configuration = [WKWebViewConfiguration new];
        configuration.websiteDataStore = WKWebsiteDataStore.nonPersistentDataStore;
        web = [[WKWebView alloc] initWithFrame:NSMakeRect(0,0,1280,720) configuration:configuration];
        NSWindow *window = [[NSWindow alloc] initWithContentRect:NSMakeRect(20,20,1280,720) styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
        window.contentView = web;
        [window orderBack:nil];
        Probe *probe = [Probe new];
        web.navigationDelegate = probe;
        NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[NSString stringWithUTF8String:argv[1]]]];
        [request setValue:[NSString stringWithUTF8String:argv[2]] forHTTPHeaderField:@"X-Gunther-Capture"];
        [web loadRequest:request];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 60*NSEC_PER_SEC), dispatch_get_main_queue(), ^{ fprintf(stderr,"WKWebView probe timed out\n"); exit(5); });
        [app run];
    }
    return 0;
}
