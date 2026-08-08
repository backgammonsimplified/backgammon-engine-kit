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
  identical(remote_sha, expected_sha) || identical(remote_ref, expected_sha)
}

if (refresh || !installed_source_matches()) {
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

if (!file.exists(file.path(library_path, "backgammonboard", "DESCRIPTION"))) {
  stop("backgammonboard installation did not become available in the target library")
}
required <- c("ggboard", "validate_xgid", "board_colors", "board_style")
missing <- setdiff(required, getNamespaceExports("backgammonboard"))
if (length(missing)) stop(paste("missing required public API:", paste(missing, collapse = ", ")))

d <- utils::packageDescription("backgammonboard", lib.loc = library_path)
remote_sha <- remote_field(d, "RemoteSha")
remote_ref <- remote_field(d, "RemoteRef")
if (!identical(remote_sha, expected_sha) && !identical(remote_ref, expected_sha)) {
  stop(paste(
    "backgammonboard source mismatch: expected", expected_sha,
    "found RemoteSha", remote_sha, "RemoteRef", remote_ref
  ))
}
cat("backgammonboard package: ", d$Package, " ", d$Version, "\n", sep = "")
cat("backgammonboard RemoteSha: ", remote_sha, "\n", sep = "")
cat("backgammonboard RemoteRef: ", remote_ref, "\n", sep = "")
cat("backgammonboard installed path: ", system.file(package = "backgammonboard", lib.loc = library_path), "\n", sep = "")
