args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3L) {
  stop("usage: install_current_backgammonboard.R <library> <commit> <refresh:true|false>")
}

library_path <- normalizePath(args[[1L]], winslash = "/", mustWork = FALSE)
expected_sha <- args[[2L]]
refresh <- tolower(args[[3L]]) %in% c("1", "true", "yes", "y")
dir.create(library_path, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(library_path, .libPaths()))

repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos[["CRAN"]]), "@CRAN@")) {
  options(repos = c(CRAN = "https://cloud.r-project.org"))
}
if (!requireNamespace("remotes", quietly = TRUE)) {
  install.packages("remotes", lib = library_path)
}

installed_sha <- ""
if (requireNamespace("backgammonboard", quietly = TRUE)) {
  d <- utils::packageDescription("backgammonboard")
  installed_sha <- ifelse(is.null(d$RemoteSha), "", as.character(d$RemoteSha))
}

if (refresh || !identical(installed_sha, expected_sha)) {
  remotes::install_github(
    "backgammonsimplified/backgammonboard",
    ref = expected_sha,
    lib = library_path,
    dependencies = NA,
    upgrade = "never",
    force = TRUE,
    quiet = FALSE,
    build = FALSE
  )
}

if (!requireNamespace("backgammonboard", quietly = TRUE)) {
  stop("backgammonboard installation did not become available")
}
required <- c("ggboard", "validate_xgid")
missing <- setdiff(required, getNamespaceExports("backgammonboard"))
if (length(missing)) stop(paste("missing required public API:", paste(missing, collapse=", ")))
d <- utils::packageDescription("backgammonboard")
remote_sha <- ifelse(is.null(d$RemoteSha), "", as.character(d$RemoteSha))
if (!identical(remote_sha, expected_sha)) {
  stop(paste("backgammonboard RemoteSha mismatch: expected", expected_sha, "found", remote_sha))
}
cat("backgammonboard package: ", d$Package, " ", d$Version, "\n", sep = "")
cat("backgammonboard RemoteSha: ", remote_sha, "\n", sep = "")
cat("backgammonboard installed path: ", system.file(package = "backgammonboard"), "\n", sep = "")
