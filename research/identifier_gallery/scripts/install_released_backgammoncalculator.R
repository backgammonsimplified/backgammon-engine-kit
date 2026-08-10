args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 4L) {
  stop("usage: install_released_backgammoncalculator.R <library> <release-ref> <resolved-commit> <refresh:true|false>")
}

library_path <- normalizePath(args[[1L]], winslash = "/", mustWork = FALSE)
requested_ref <- args[[2L]]
resolved_commit <- args[[3L]]
refresh <- tolower(args[[4L]]) %in% c("1", "true", "yes", "y")
dir.create(library_path, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(library_path, .libPaths()))

repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos[["CRAN"]]), "@CRAN@")) {
  options(repos = c(CRAN = "https://cloud.r-project.org"))
}
remote_field <- function(d, field) {
  value <- d[[field]]
  if (is.null(value)) "" else as.character(value)
}

installed_release_matches <- function() {
  description_path <- file.path(library_path, "backgammoncalculator", "DESCRIPTION")
  if (!file.exists(description_path)) return(FALSE)
  d <- utils::packageDescription("backgammoncalculator", lib.loc = library_path)
  identical(as.character(d$Version), "0.2.0") && (
    identical(remote_field(d, "RemoteSha"), resolved_commit) ||
      identical(remote_field(d, "GithubSHA1"), resolved_commit) ||
      identical(remote_field(d, "RemoteRef"), requested_ref) ||
      identical(remote_field(d, "GithubRef"), requested_ref)
  )
}

if (refresh || !installed_release_matches()) {
  if (!requireNamespace("remotes", quietly = TRUE)) {
    install.packages("remotes", lib = library_path)
  }
  remotes::install_github(
    "backgammonsimplified/backgammoncalculator",
    ref = requested_ref,
    lib = library_path,
    dependencies = NA,
    upgrade = "never",
    force = TRUE,
    quiet = FALSE,
    build = FALSE
  )
}

if (!installed_release_matches()) {
  stop(paste(
    "backgammoncalculator release provenance mismatch: requested",
    requested_ref, "resolved", resolved_commit
  ))
}
required <- c("xgid_to_gnuid", "gnuid_to_xgid", "position_from_xgid", "position_from_gnuid")
missing <- setdiff(required, getNamespaceExports("backgammoncalculator"))
if (length(missing)) stop(paste("missing required public API:", paste(missing, collapse = ", ")))

d <- utils::packageDescription("backgammoncalculator", lib.loc = library_path)
cat("backgammoncalculator package: ", d$Package, " ", d$Version, "\n", sep = "")
cat("backgammoncalculator requested release ref: ", requested_ref, "\n", sep = "")
cat("backgammoncalculator resolved release commit: ", resolved_commit, "\n", sep = "")
cat("backgammoncalculator RemoteSha: ", remote_field(d, "RemoteSha"), "\n", sep = "")
cat("backgammoncalculator RemoteRef: ", remote_field(d, "RemoteRef"), "\n", sep = "")
cat("backgammoncalculator installed path: ", system.file(package = "backgammoncalculator", lib.loc = library_path), "\n", sep = "")
