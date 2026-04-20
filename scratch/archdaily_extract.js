var result = {};

// Title
result.title = document.querySelector('h1') ? document.querySelector('h1').textContent.trim() : '';

// LD+JSON structured data
var ldScripts = document.querySelectorAll('script[type$=json]');
result.ldJsonCount = ldScripts.length;
result.ldJson = [];
ldScripts.forEach(function(s) {
    try { result.ldJson.push(JSON.parse(s.textContent)); } catch(e) {}
});

// All images from adsttc CDN with alt text
var allImgs = document.querySelectorAll('img');
result.imageCount = allImgs.length;
result.uniqueImages = [];
var seen = {};
allImgs.forEach(function(img) {
    var src = img.src || '';
    if (src.indexOf('adsttc.com') === -1) return;
    if (src.indexOf('logo') !== -1 || src.indexOf('loader') !== -1) return;
    var base = src.replace(/\/(newsletter|medium_jpg|large_jpg|thumb|slideshow)\//g, '/SIZE/');
    base = base.split('?')[0];
    if (!seen[base]) {
        seen[base] = true;
        var sizeMatch = src.match(/\/(newsletter|medium_jpg|large_jpg|thumb|slideshow)\//);
        result.uniqueImages.push({
            src: src,
            alt: img.alt,
            size: sizeMatch ? sizeMatch[1] : 'unknown'
        });
    }
});

// Meta
var metaDesc = document.querySelector('meta[name=description]');
result.metaDescription = metaDesc ? metaDesc.content : '';

JSON.stringify(result);
