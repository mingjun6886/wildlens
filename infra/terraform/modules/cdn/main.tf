# --- One distribution, two origins -------------------------------------------
#
# The site and the API are served from the same hostname. That is the whole point:
# the browser never makes a cross-origin request, so there is no CORS to configure
# for the API in either environment and no second origin for a single-valued
# ALLOWED_ORIGIN to fail to express.
#
#   https://d111.cloudfront.net/v1/*  -> API Gateway   (no caching)
#   https://d111.cloudfront.net/*     -> S3, private   (cached)
#
# The cost is the behaviour below: if the API path were ever cached, CloudFront
# would serve one signed-in user's response to another. That is a worse failure
# than any CORS misconfiguration, which is why it is asserted by an explicit
# managed policy rather than left to a default.

# --- Reading the private bucket ----------------------------------------------
#
# Origin Access Control, not the older Origin Access Identity. OAC signs requests
# with SigV4 and works with SSE-KMS; OAI is legacy and AWS recommends against new
# use of it.
resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${var.name_prefix}-web-oac"
  description                       = "Lets this distribution, and nothing else, read the web bucket."
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# The bucket stays private: public access is blocked on all four buckets, and this
# policy grants read to the distribution's service principal only, narrowed by
# ARN so another distribution in the same account cannot read it.
resource "aws_s3_bucket_policy" "web" {
  bucket = var.web_bucket_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowThisDistributionToRead"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${var.web_bucket_arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site.arn }
      }
    }]
  })
}

# --- Distribution ------------------------------------------------------------

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "${var.name_prefix} site and API"
  default_root_object = "index.html"
  price_class         = var.price_class

  # Only IPv4 is on. Dual-stack would be free and is the better default, but every
  # client here is a browser reaching an IPv4-capable network, and leaving it off
  # keeps one fewer path untested.
  is_ipv6_enabled = false

  origin {
    origin_id                = "web"
    domain_name              = var.web_bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  origin {
    origin_id   = "api"
    domain_name = var.api_domain_name

    custom_origin_config {
      http_port  = 80
      https_port = 443
      # API Gateway only speaks HTTPS, and the TLS version is pinned rather than
      # left to the default so a downgrade is a visible change here.
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # --- The site ---------------------------------------------------------------
  default_cache_behavior {
    target_origin_id       = "web"
    viewer_protocol_policy = "redirect-to-https"

    allowed_methods = ["GET", "HEAD", "OPTIONS"]
    cached_methods  = ["GET", "HEAD"]

    # CachingOptimized, an AWS managed policy. Vite fingerprints asset filenames,
    # so a long cache is safe for everything except index.html — and index.html is
    # small enough that the invalidation in scripts/deploy-web.sh handles it.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  # --- The API ----------------------------------------------------------------
  #
  # Declared second but matched first: CloudFront evaluates ordered behaviours
  # before the default one.
  ordered_cache_behavior {
    path_pattern           = "${var.api_path_prefix}/*"
    target_origin_id       = "api"
    viewer_protocol_policy = "redirect-to-https"

    # Every method, because the API has POST routes. Omitting them returns 403
    # from CloudFront with no indication that the method was the problem.
    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods  = ["GET", "HEAD"]

    # CachingDisabled. The single most important line in this file.
    #
    # Every response here is specific to the caller: their records, their signed
    # URLs. Caching any of it would serve one signed-in user's data to another,
    # which is a far worse failure than the CORS problems this distribution
    # exists to avoid. Named by managed-policy id rather than assembled from
    # min_ttl/default_ttl so there is no arithmetic to get wrong.
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"

    # AllViewerExceptHostHeader. Two reasons, and both matter.
    #
    # Authorization must reach the origin or every request is a 401. The plain
    # AllViewer policy would forward it — and would also forward the viewer's Host
    # header, which API Gateway rejects because it routes on its own hostname. The
    # failure is a 403 from API Gateway that names nothing.
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
  }

  # No custom_error_response, and that is deliberate rather than an omission.
  #
  # A single-page app usually maps 403 and 404 to index.html so client-side routes
  # work on a refresh. Those rules apply to the whole distribution, including the
  # API behaviour — so an API 404 would come back as index.html with status 200,
  # and a client could not tell "no such record" from "here is the page again".
  #
  # This client has one page and no client-side routing, so the rule buys nothing
  # and the trap is avoided by not setting it.

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # The default *.cloudfront.net certificate. A custom domain would need an ACM
    # certificate in us-east-1, which is a separate provider alias for a project
    # with no domain name.
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1"
  }
}
