args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 4L) {
  stop("usage: install_current_backgammonboard.R <library> <release-ref> <resolved-commit> <refresh:true|false>")
}

library_path <- normalizePath(args[[1L]], winslash = "/", mustWork = FALSE)
requested_ref <- args[[2L]]
expected_sha <- args[[3L]]
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

installed_source_matches <- function() {
  description_path <- file.path(library_path, "backgammonboard", "DESCRIPTION")
  if (!file.exists(description_path)) return(FALSE)
  d <- utils::packageDescription("backgammonboard", lib.loc = library_path)
  remote_sha <- remote_field(d, "RemoteSha")
  remote_ref <- remote_field(d, "RemoteRef")
  github_sha1 <- remote_field(d, "GithubSHA1")
  github_ref <- remote_field(d, "GithubRef")
  identical(remote_sha, expected_sha) || identical(github_sha1, expected_sha) ||
    identical(remote_ref, requested_ref) || identical(github_ref, requested_ref)
}

if (refresh || !installed_source_matches()) {
  if (!requireNamespace("remotes", quietly = TRUE)) {
    install.packages("remotes", lib = library_path)
  }
  remotes::install_github(
    "backgammonsimplified/backgammonboard",
    ref = requested_ref,
    lib = library_path,
    dependencies = NA,
    upgrade = "never",
    force = TRUE,
    quiet = FALSE,
    build = FALSE
  )
}

if (!file.exists(file.path(library_path, "backgammonboard", "DESCRIPTION"))) {
  stop("backgammonboard installation did not become available in the target library")
}
required <- c("ggboard", "validate_xgid", "board_colors", "board_style")
missing <- setdiff(required, getNamespaceExports("backgammonboard"))
if (length(missing)) stop(paste("missing required public API:", paste(missing, collapse = ", ")))

d <- utils::packageDescription("backgammonboard", lib.loc = library_path)
remote_sha <- remote_field(d, "RemoteSha")
remote_ref <- remote_field(d, "RemoteRef")
github_sha1 <- remote_field(d, "GithubSHA1")
github_ref <- remote_field(d, "GithubRef")
matches_release <- identical(remote_sha, expected_sha) ||
  identical(github_sha1, expected_sha) || identical(remote_ref, requested_ref) ||
  identical(github_ref, requested_ref)
if (!matches_release) {
  stop(paste(
    "backgammonboard source mismatch: requested", requested_ref,
    "resolved", expected_sha, "found RemoteSha", remote_sha,
    "RemoteRef", remote_ref, "GithubSHA1", github_sha1,
    "GithubRef", github_ref
  ))
}
cat("backgammonboard package: ", d$Package, " ", d$Version, "\n", sep = "")
cat("backgammonboard requested release ref: ", requested_ref, "\n", sep = "")
cat("backgammonboard resolved release commit: ", expected_sha, "\n", sep = "")
cat("backgammonboard RemoteSha: ", remote_sha, "\n", sep = "")
cat("backgammonboard RemoteRef: ", remote_ref, "\n", sep = "")
cat("backgammonboard installed path: ", system.file(package = "backgammonboard", lib.loc = library_path), "\n", sep = "")
