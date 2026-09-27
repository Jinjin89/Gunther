#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <PDFKit/PDFKit.h>
#import <Vision/Vision.h>
#import <math.h>

static const long long maximumSourcePixels = 400000000;
static const long long maximumSourceDimension = 100000;
static const NSInteger maximumOCRDimension = 4096;

static void fail(NSString *message) {
    fprintf(stderr, "%s\n", message.UTF8String);
    exit(2);
}

static CGImageRef createNormalizedOCRImage(CGImageRef source) {
    size_t width = CGImageGetWidth(source);
    size_t height = CGImageGetHeight(source);
    if (width < 1 || height < 1 || width > maximumOCRDimension || height > maximumOCRDimension) {
        return nil;
    }
    CGColorSpaceRef colorSpace = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    if (colorSpace == nil) return nil;
    CGContextRef context = CGBitmapContextCreate(
        NULL, width, height, 8, width * 4, colorSpace,
        kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big
    );
    CGColorSpaceRelease(colorSpace);
    if (context == nil) return nil;
    CGContextSetInterpolationQuality(context, kCGInterpolationHigh);
    CGContextDrawImage(context, CGRectMake(0, 0, width, height), source);
    CGImageRef normalized = CGBitmapContextCreateImage(context);
    CGContextRelease(context);
    return normalized;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 4) fail(@"missing arguments");
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        NSInteger requestedPage = [[NSString stringWithUTF8String:argv[2]] integerValue];
        NSString *languagesValue = [NSString stringWithUTF8String:argv[3]];
        NSArray<NSString *> *languages = [languagesValue componentsSeparatedByString:@","];
        NSImage *image = nil;
        CGImageRef ownedImage = nil;
        CGImageRef cgImage = nil;

        if (requestedPage > 0 && [path.lowercaseString hasSuffix:@".pdf"]) {
            PDFDocument *document = [[PDFDocument alloc] initWithURL:[NSURL fileURLWithPath:path]];
            if (document == nil || requestedPage > document.pageCount) fail(@"PDF page unavailable");
            PDFPage *page = [document pageAtIndex:(NSUInteger)(requestedPage - 1)];
            if (page == nil) fail(@"PDF page unavailable");
            NSRect bounds = [page boundsForBox:kPDFDisplayBoxMediaBox];
            CGFloat scale = MIN(2.0, 2400.0 / MAX(bounds.size.width, bounds.size.height));
            NSSize size = NSMakeSize(
                MAX(1.0, ceil(bounds.size.width * scale)),
                MAX(1.0, ceil(bounds.size.height * scale))
            );
            if (size.width * size.height > 20000000.0) fail(@"PDF raster exceeds safe limit");
            image = [page thumbnailOfSize:size forBox:kPDFDisplayBoxMediaBox];
            if (image == nil) fail(@"image cannot be decoded");
            NSRect proposed = NSMakeRect(0, 0, image.size.width, image.size.height);
            cgImage = [image CGImageForProposedRect:&proposed context:nil hints:nil];
        } else {
            NSURL *url = [NSURL fileURLWithPath:path];
            CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
            if (source == nil || CGImageSourceGetCount(source) < 1) {
                if (source != nil) CFRelease(source);
                fail(@"image cannot be decoded");
            }
            NSDictionary *properties = CFBridgingRelease(
                CGImageSourceCopyPropertiesAtIndex(source, 0, NULL)
            );
            NSNumber *widthValue = properties[(__bridge NSString *)kCGImagePropertyPixelWidth];
            NSNumber *heightValue = properties[(__bridge NSString *)kCGImagePropertyPixelHeight];
            long long width = widthValue.longLongValue;
            long long height = heightValue.longLongValue;
            BOOL invalidDimensions = width < 1 || height < 1
                || width > maximumSourceDimension || height > maximumSourceDimension
                || width > maximumSourcePixels / height;
            if (invalidDimensions) {
                CFRelease(source);
                fail(@"image dimensions exceed safe OCR limits");
            }
            NSDictionary *options = @{
                (__bridge NSString *)kCGImageSourceCreateThumbnailFromImageAlways: @YES,
                (__bridge NSString *)kCGImageSourceCreateThumbnailWithTransform: @YES,
                (__bridge NSString *)kCGImageSourceThumbnailMaxPixelSize: @(maximumOCRDimension),
                (__bridge NSString *)kCGImageSourceShouldCacheImmediately: @YES,
            };
            ownedImage = CGImageSourceCreateThumbnailAtIndex(
                source, 0, (__bridge CFDictionaryRef)options
            );
            CFRelease(source);
            cgImage = ownedImage;
        }
        if (cgImage == nil) fail(@"image raster unavailable");
        CGImageRef normalizedImage = createNormalizedOCRImage(cgImage);
        if (ownedImage != nil) CGImageRelease(ownedImage);
        ownedImage = normalizedImage;
        cgImage = ownedImage;
        if (cgImage == nil) fail(@"image raster exceeds safe OCR limits");

        VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
        request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        request.usesLanguageCorrection = YES;
        NSError *error = nil;
        NSArray<NSString *> *supported = [VNRecognizeTextRequest
            supportedRecognitionLanguagesForTextRecognitionLevel:VNRequestTextRecognitionLevelAccurate
            revision:request.revision
            error:&error
        ];
        if (supported == nil) {
            fail([NSString stringWithFormat:@"Vision language discovery failed: %@", error.localizedDescription]);
        }
        NSMutableArray<NSString *> *selected = [NSMutableArray array];
        for (NSString *language in languages) {
            if ([supported containsObject:language]) [selected addObject:language];
        }
        if (selected.count > 0) request.recognitionLanguages = selected;
        if (@available(macOS 13.0, *)) {
            request.automaticallyDetectsLanguage = YES;
        }
        error = nil;
        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:cgImage options:@{}];
        if (![handler performRequests:@[request] error:&error]) {
            fail([NSString stringWithFormat:@"Vision request failed: %@", error.localizedDescription]);
        }

        NSArray<VNRecognizedTextObservation *> *observations = [request.results sortedArrayUsingComparator:
            ^NSComparisonResult(VNRecognizedTextObservation *left, VNRecognizedTextObservation *right) {
                CGFloat leftTop = 1.0 - CGRectGetMaxY(left.boundingBox);
                CGFloat rightTop = 1.0 - CGRectGetMaxY(right.boundingBox);
                if (fabs(leftTop - rightTop) > 0.005) {
                    return leftTop < rightTop ? NSOrderedAscending : NSOrderedDescending;
                }
                return CGRectGetMinX(left.boundingBox) < CGRectGetMinX(right.boundingBox)
                    ? NSOrderedAscending : NSOrderedDescending;
            }
        ];
        for (VNRecognizedTextObservation *observation in observations) {
            VNRecognizedText *candidate = [[observation topCandidates:1] firstObject];
            if (candidate == nil) continue;
            NSString *clean = [[candidate.string stringByReplacingOccurrencesOfString:@"\t" withString:@" "]
                stringByReplacingOccurrencesOfString:@"\n" withString:@" "];
            clean = [clean stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
            if (clean.length == 0) continue;
            CGRect box = observation.boundingBox;
            NSInteger page = requestedPage > 0 ? requestedPage : 1;
            NSInteger left = lround(CGRectGetMinX(box) * 1000000.0);
            NSInteger top = lround((1.0 - CGRectGetMaxY(box)) * 1000000.0);
            NSInteger width = lround(CGRectGetWidth(box) * 1000000.0);
            NSInteger height = lround(CGRectGetHeight(box) * 1000000.0);
            printf(
                "%ld\t%ld\t%ld\t%ld\t%ld\t%.6f\t%s\n",
                (long)page, (long)left, (long)top, (long)width, (long)height,
                candidate.confidence, clean.UTF8String
            );
        }
        if (ownedImage != nil) CGImageRelease(ownedImage);
    }
    return 0;
}
