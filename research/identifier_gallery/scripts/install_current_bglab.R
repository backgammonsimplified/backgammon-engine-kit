args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2L) {
  stop("usage: install_current_bglab.R <library> <refresh:true|false>")
}

library_path <- normalizePath(args[[1L]], winslash = "/", mustWork = FALSE)
refresh <- tolower(args[[2L]]) %in% c("1", "true", "yes", "y")
dir.create(library_path, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(library_path, .libPaths()))

repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos[["CRAN"]]), "@CRAN@")) {
  options(repos = c(CRAN = "https://cloud.r-project.org"))
}

if (!requireNamespace("remotes", quietly = TRUE)) {
  install.packages("remotes", lib = library_path)
}

if (refresh || !requireNamespace("bglab", quietly = TRUE)) {
  remotes::install_github(
    "lassehjorthmadsen/bglab",
    ref = "main",
    lib = library_path,
    dependencies = NA,
    upgrade = "never",
    force = TRUE,
    quiet = FALSE,
    build = FALSE
  )
}

if (!requireNamespace("bglab", quietly = TRUE)) {
  stop("bglab installation did not become available")
}
if (!exists("gnuid2xgid", envir = asNamespace("bglab"), inherits = FALSE)) {
  stop("installed bglab does not export gnuid2xgid")
}

d <- utils::packageDescription("bglab")
cat("bglab package: ", d$Package, " ", d$Version, "\n", sep = "")
cat("bglab RemoteSha: ", ifelse(is.null(d$RemoteSha), "not recorded", d$RemoteSha), "\n", sep = "")
cat("bglab RemoteRef: ", ifelse(is.null(d$RemoteRef), "not recorded", d$RemoteRef), "\n", sep = "")
cat("bglab installed path: ", system.file(package = "bglab"), "\n", sep = "")
